"""Collector tests run against a MOCKED transport with synthetic payloads (no network)."""
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from collector.common import (HttpClient, SourceUnavailable, canonical_url, html_to_text, parse_experience,  # noqa: E402
                              remote_status, to_iso_date)
from collector.pipeline import Pipeline  # noqa: E402
from collector.sources import Arbeitnow, Remotive  # noqa: E402


def arbeit_item(i, title="Python Developer", company=None, desc=None):
    return {"slug": f"job-{i}", "company_name": company or f"Co{i}", "title": f"{title} {i}",
            "description": desc or "<p>We need <b>Python</b> and Django. 3+ years of experience required.</p><ul><li>Docker</li></ul>",
            "remote": i % 2 == 0, "url": f"https://www.arbeitnow.com/jobs/job-{i}?utm=x", "tags": ["backend"],
            "job_types": ["full time"], "location": "Berlin", "created_at": 1760000000 + i}


def handler_factory(pages, fail_first=0, calls=None):
    state = {"fails": fail_first}

    def handler(request: httpx.Request):
        if calls is not None:
            calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if state["fails"] > 0:
            state["fails"] -= 1
            return httpx.Response(429, headers={"Retry-After": "0"})
        page = int(request.url.params.get("page", 1))
        data = pages.get(page, [])
        return httpx.Response(200, json={"data": data})
    return handler


def make_pipe(tmp_path, pages, **kw):
    http = HttpClient("test@example.com", min_interval=0, transport=httpx.MockTransport(handler_factory(pages, **kw.pop("hkw", {}))),
                      sleep=lambda s: None)
    return Pipeline(tmp_path / "raw", tmp_path / "jobs.json", http, [Arbeitnow()], min_total=3, **kw)


def test_end_to_end_normalises_and_validates(tmp_path):
    pipe = make_pipe(tmp_path, {1: [arbeit_item(i) for i in range(1, 4)], 2: [arbeit_item(i) for i in range(4, 6)]})
    pipe.run()
    rep = pipe.build()
    data = json.loads((tmp_path / "jobs.json").read_text())
    assert rep["total_jobs"] == 5 == data["meta"]["count"] and rep["meets_minimum"]
    j = data["jobs"][0]
    for field in ("job_id", "title", "company", "location", "experience_required", "skills", "description", "source_url"):
        assert j[field]
    assert j["job_id"].startswith("j_") and "python" in j["skills"] and "docker" in j["skills"]
    assert j["min_experience_years"] == 3 and j["experience_required"] == "3+ years"
    assert "<" not in j["description"] and j["source_name"] == "arbeitnow" and j["scraped_at"]
    assert (tmp_path / "quality_report.json").exists()


def test_resumable_and_idempotent(tmp_path):
    pages = {1: [arbeit_item(i) for i in range(1, 4)], 2: [arbeit_item(i) for i in range(4, 6)]}
    make_pipe(tmp_path, pages).run()
    first = (tmp_path / "raw" / "arbeitnow.jsonl").read_text()
    # second run: state says done -> no new fetches and no duplicates
    p2 = make_pipe(tmp_path, pages)
    p2.run()
    assert (tmp_path / "raw" / "arbeitnow.jsonl").read_text() == first
    # simulate a crash: not done, cursor at page 2; page 2 is re-fetched and deduped
    st = json.loads((tmp_path / "raw" / "state.json").read_text())
    st["arbeitnow"] = {"cursor": 2, "done": False}
    (tmp_path / "raw" / "state.json").write_text(json.dumps(st))
    p3 = make_pipe(tmp_path, pages)
    p3.run()
    assert len((tmp_path / "raw" / "arbeitnow.jsonl").read_text().splitlines()) == 5
    assert p3.counters["arbeitnow"].duplicates == 2
    assert p3.build()["total_jobs"] == 5


def test_dedupe_across_keys_and_malformed_records(tmp_path):
    items = [arbeit_item(1), arbeit_item(2),
             {**arbeit_item(3), "slug": "other-slug", "url": "https://www.arbeitnow.com/jobs/job-1"},  # same URL as 1
             {**arbeit_item(4), "title": "Python Developer 2", "company_name": "Co2", "slug": "x4", "url": "https://a.test/other"},  # same title/company/location as 2
             {"slug": "bad", "title": "", "company_name": "X", "url": "https://x.test/1"},  # invalid
             {"title": "no slug"},  # malformed
             arbeit_item(7, title="Barista", desc="<p>Make coffee</p>")]  # non-tech
    pipe = make_pipe(tmp_path, {1: items})
    pipe.run()
    rep = pipe.build()
    c = pipe.counters["arbeitnow"]
    assert c.invalid == 2 and c.filtered_non_tech == 1
    assert rep["total_jobs"] == 2 and rep["duplicates_removed"]["same_url"] == 1 and rep["duplicates_removed"]["same_title_company_location"] == 1


def test_shortfall_is_reported_not_hidden(tmp_path):
    pipe = make_pipe(tmp_path, {1: [arbeit_item(1)]})
    pipe.run()
    rep = pipe.build()
    assert rep["meets_minimum"] is False and "Only 1" in rep["shortfall_note"]


def test_retries_with_backoff_on_429(tmp_path):
    pipe = make_pipe(tmp_path, {1: [arbeit_item(1)]}, hkw={"fail_first": 2})
    pipe.run()
    assert pipe.counters["arbeitnow"].accepted == 1


def test_source_failure_is_contained_and_progress_kept(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(500)
    http = HttpClient("t@e.com", min_interval=0, max_retries=2, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    pipe = Pipeline(tmp_path / "raw", tmp_path / "jobs.json", http, [Arbeitnow()])
    pipe.run()  # must not raise
    assert pipe.build()["total_jobs"] == 0
    with pytest.raises(SourceUnavailable):
        http.get_json("https://www.arbeitnow.com/api/job-board-api")


def test_robots_disallow_skips_source(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /api/")
        raise AssertionError("must not fetch API when robots disallows")
    http = HttpClient("t@e.com", min_interval=0, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    pipe = Pipeline(tmp_path / "raw", tmp_path / "jobs.json", http, [Arbeitnow()])
    pipe.run()
    assert pipe.counters["arbeitnow"].fetched == 0


def test_remotive_parsing():
    payload = {"jobs": [{"id": 9, "url": "https://remotive.com/remote-jobs/x-9", "title": "Backend Engineer", "company_name": "Acme",
                         "tags": ["python"], "job_type": "full_time", "publication_date": "2026-10-01T10:00:00",
                         "candidate_required_location": "Worldwide", "salary": "$50k", "description": "<p>Python</p>"}]}
    http = HttpClient("t@e.com", min_interval=0, sleep=lambda s: None,
                      transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    (raw, _), = list(Remotive().fetch(http, None))
    assert raw["source_id"] == "9" and raw["remote"] is True and raw["posted"] == "2026-10-01"


def test_helpers():
    assert html_to_text("<p>Hi&nbsp;<b>there</b></p><script>x()</script><li>a</li>").startswith("Hi")
    assert canonical_url("HTTPS://Example.com/a/b/?utm=1#f") == "https://example.com/a/b"
    assert to_iso_date(1760000000) == "2025-10-09" and to_iso_date("2026-01-02T03:04:05Z") == "2026-01-02" and to_iso_date("junk") is None
    assert parse_experience("Junior Dev", "")["experience_level"] == "entry"
    assert parse_experience("Dev", "Requires 3-5 years of experience")["experience_required"] == "3-5 years"
    assert parse_experience("Dev", "We have 40 years of experience as a company")["min_experience_years"] is None
    assert parse_experience("Senior Dev", "")["experience_level"] == "senior"
    assert remote_status(True) == "remote" and remote_status(None, "Hybrid - London") == "hybrid" and remote_status(None, "Berlin") == "unknown"
