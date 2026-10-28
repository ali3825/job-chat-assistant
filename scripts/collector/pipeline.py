"""Resumable, idempotent collection pipeline: fetch -> normalise -> validate -> append (jsonl) -> dedupe -> jobs.json."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from pydantic import ValidationError  # noqa: E402

from app.core.skills import extract_skills  # noqa: E402
from app.schemas import Job  # noqa: E402

from .common import (HttpClient, SourceUnavailable, canonical_url, employment_type, fingerprint,  # noqa: E402
                     html_to_text, make_job_id, now_iso, parse_experience, remote_status)
from .sources import Source  # noqa: E402

log = logging.getLogger("collector")
TECH_TITLE = re.compile(r"\b(engineer|developer|programmer|software|devops|sre|data|analyst|scientist|qa|tester|"
                        r"architect|full[- ]?stack|front[- ]?end|back[- ]?end|machine learning|ml|ai|cloud|security|"
                        r"it support|sysadmin|web|mobile|android|ios|product manager)\b", re.I)


@dataclass
class Counters:
    fetched: int = 0
    accepted: int = 0
    duplicates: int = 0
    invalid: int = 0
    filtered_non_tech: int = 0


@dataclass
class Pipeline:
    work_dir: Path
    out_file: Path
    http: HttpClient
    sources: list[Source]
    target: int = 2000
    min_total: int = 1500
    per_source_cap: int = 1000
    tech_only: bool = True
    counters: dict[str, Counters] = field(default_factory=dict)

    # ------------------------------------------------------------- state
    @property
    def state_path(self) -> Path:
        return self.work_dir / "state.json"

    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_state(self, state: dict) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        os.replace(tmp, self.state_path)

    def _jsonl(self, source: str) -> Path:
        return self.work_dir / f"{source}.jsonl"

    def _read_jsonl(self, source: str) -> list[dict]:
        p, out = self._jsonl(source), []
        if not p.exists():
            return out
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                log.warning("skipping a corrupt line in %s (likely an interrupted write)", p.name)
        return out

    # ------------------------------------------------------------- collection
    def run(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        state = self._load_state()
        for src in self.sources:
            c = self.counters.setdefault(src.name, Counters())
            ok, why = src.available()
            if not ok:
                log.info("[%s] skipped: %s", src.name, why)
                continue
            st = state.setdefault(src.name, {"cursor": None, "done": False})
            if st["done"]:
                log.info("[%s] already complete (delete %s to re-fetch)", src.name, self.state_path.name)
                continue
            probe = src.api_url or "https://" + src.terms_url.split("/")[2] + "/"
            if not self.http.robots_allows(probe):
                log.warning("[%s] robots.txt disallows or is unreachable; skipping", src.name)
                continue
            self.http.min_interval = src.min_interval
            existing = self._read_jsonl(src.name)
            seen_ids = {r["_source_id"] for r in existing}
            kept = len(existing)
            log.info("[%s] starting at cursor=%r with %d records already saved", src.name, st["cursor"], kept)
            try:
                with self._jsonl(src.name).open("a", encoding="utf-8") as fh:
                    for raw, cursor in src.fetch(self.http, st["cursor"]):
                        c.fetched += 1
                        sid = raw.get("source_id", "")
                        if sid in seen_ids:
                            c.duplicates += 1
                            st["cursor"] = cursor
                            continue
                        rec, reason = self.normalize(raw, src.name)
                        if rec is None:
                            setattr(c, reason, getattr(c, reason) + 1)
                        else:
                            fh.write(json.dumps({**rec, "_source_id": sid}, ensure_ascii=False) + "\n")
                            fh.flush()
                            seen_ids.add(sid)
                            c.accepted += 1
                            kept += 1
                        if st["cursor"] != cursor:
                            st["cursor"] = cursor
                            self._save_state(state)  # checkpoint at page boundaries
                        if kept >= self.per_source_cap:
                            log.info("[%s] reached per-source cap (%d)", src.name, kept)
                            break
                    else:
                        st["done"] = True
            except SourceUnavailable as exc:
                log.error("[%s] stopped: %s. Progress is saved; re-run to resume.", src.name, exc)
            self._save_state(state)
            log.info("[%s] %d fetched, %d new, %d dup, %d invalid, %d non-tech", src.name, c.fetched, c.accepted,
                     c.duplicates, c.invalid, c.filtered_non_tech)

    # ------------------------------------------------------------- normalisation
    def normalize(self, raw: dict, source: str) -> tuple[dict | None, str]:
        try:
            title, company, url = raw["title"].strip(), raw["company"].strip(), raw["url"].strip()
            sid = raw["source_id"]
        except (KeyError, AttributeError):
            return None, "invalid"
        if not (title and company and sid and url.startswith("http")):
            return None, "invalid"
        desc = html_to_text(raw.get("description_html"))
        tags = " ".join(raw.get("tags") or [])
        skills = extract_skills(f"{title}\n{tags}\n{desc}")
        if self.tech_only and not skills and not TECH_TITLE.search(title):
            return None, "filtered_non_tech"
        scraped = now_iso()
        location = raw.get("location") or "Not specified"
        rec = {
            "job_id": make_job_id(source, sid), "title": title[:300], "company": company[:300], "location": location[:200],
            **parse_experience(title, desc), "skills": skills, "description": desc or "Not provided by source.",
            "source_url": url, "employment_type": employment_type(raw.get("employment")),
            "remote_status": remote_status(raw.get("remote"), location, title), "posted_date": raw.get("posted"),
            "application_url": raw.get("application_url") or url, "salary": raw.get("salary"), "source_name": source,
            "scraped_at": scraped, "last_verified_at": scraped, "listing_status": "listed",
        }
        try:
            Job.model_validate(rec)
        except ValidationError:
            return None, "invalid"
        return rec, ""

    # ------------------------------------------------------------- build final dataset
    def build(self) -> dict:
        all_recs: list[dict] = []
        for src in self.sources:
            all_recs += self._read_jsonl(src.name)
        seen_src, seen_url, seen_fp = set(), set(), set()
        unique, dup_counts = [], Counter()
        for r in all_recs:
            k1, k2 = (r["source_name"], r["_source_id"]), canonical_url(r["source_url"])
            k3 = fingerprint(r["title"], r["company"], r["location"])
            if k1 in seen_src: dup_counts["same_source_id"] += 1; continue
            if k2 in seen_url: dup_counts["same_url"] += 1; continue
            if k3 in seen_fp: dup_counts["same_title_company_location"] += 1; continue
            seen_src.add(k1); seen_url.add(k2); seen_fp.add(k3)
            r = {k: v for k, v in r.items() if k != "_source_id"}
            unique.append(r)
        final = self._cap_round_robin(unique, self.target)
        jobs = []
        for r in final:
            try:
                jobs.append(Job.model_validate(r).model_dump())
            except ValidationError:
                dup_counts["invalid_at_build"] += 1
        report = self._report(jobs, len(all_recs), dup_counts)
        payload = {"meta": {"generated_at": now_iso(), "count": len(jobs), "sources": report["by_source"],
                            "note": "Real listings collected from public job APIs; see data/quality_report.json."},
                   "jobs": jobs}
        self.out_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.out_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.out_file)
        (self.out_file.parent / "quality_report.json").write_text(json.dumps(report, indent=2))
        return report

    @staticmethod
    def _cap_round_robin(recs: list[dict], cap: int) -> list[dict]:
        if len(recs) <= cap:
            return recs
        buckets: dict[str, list[dict]] = {}
        for r in recs:
            buckets.setdefault(r["source_name"], []).append(r)
        out: list[dict] = []
        while len(out) < cap and any(buckets.values()):
            for b in buckets.values():
                if b and len(out) < cap:
                    out.append(b.pop(0))
        return out

    def _report(self, jobs: list[dict], raw_total: int, dups: Counter) -> dict:
        n = len(jobs) or 1
        pct = lambda x: round(100 * x / n, 1)
        report = {
            "total_jobs": len(jobs), "raw_records_before_dedupe": raw_total, "duplicates_removed": dict(dups),
            "by_source": dict(Counter(j["source_name"] for j in jobs)),
            "with_skills_pct": pct(sum(bool(j["skills"]) for j in jobs)),
            "with_experience_stated_pct": pct(sum(j["min_experience_years"] is not None for j in jobs)),
            "with_posted_date_pct": pct(sum(bool(j["posted_date"]) for j in jobs)),
            "remote_status": dict(Counter(j["remote_status"] for j in jobs)),
            "collector_counters": {k: vars(v) for k, v in self.counters.items()},
            "target_range": [self.min_total, self.target],
            "meets_minimum": len(jobs) >= self.min_total,
            "caveats": [
                "last_verified_at equals scraped_at: it only means the source listed the job at that time.",
                "Listings may have closed since collection; the app labels them accordingly.",
                "Experience and skills are extracted heuristically from descriptions and can be wrong.",
            ],
        }
        if not report["meets_minimum"]:
            report["shortfall_note"] = (f"Only {len(jobs)} listings collected (< {self.min_total}). Enable more sources "
                                        "(e.g. Adzuna with free keys), re-run to resume, or use --no-tech-filter.")
        return report
