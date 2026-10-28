"""Job sources. Each yields RawJob dicts with a common shape and exposes a resumable cursor.

IMPORTANT: response schemas below follow each provider's public documentation as I know it. They
could NOT be verified against the live APIs from the build sandbox (outbound access to job sites
was blocked), so parsing is defensive: malformed records are skipped and counted, not fatal.
Read each provider's terms before running (links in README) and keep requests modest.
"""
from __future__ import annotations

import logging
import os
from typing import Iterator

from .common import HttpClient, SourceUnavailable, to_iso_date

log = logging.getLogger("collector")


class Source:
    name = "base"
    api_url = ""
    terms_url = ""
    min_interval = 1.5
    needs_key = False

    def available(self) -> tuple[bool, str]:
        return True, ""

    def fetch(self, http: HttpClient, cursor):  # -> Iterator[(raw_job, next_cursor)]
        raise NotImplementedError


def _s(v) -> str:
    return str(v).strip() if v not in (None, "") else ""


class Arbeitnow(Source):
    name = "arbeitnow"
    api_url = "https://www.arbeitnow.com/api/job-board-api"
    terms_url = "https://www.arbeitnow.com/api"
    min_interval = 1.5

    def fetch(self, http, cursor):
        page = int(cursor or 1)
        while True:
            data = http.get_json(self.api_url, {"page": page})
            items = data.get("data") if isinstance(data, dict) else None
            if not items:
                return
            for it in items:
                yield {
                    "source_id": _s(it.get("slug")), "title": _s(it.get("title")), "company": _s(it.get("company_name")),
                    "location": _s(it.get("location")), "description_html": it.get("description") or "",
                    "url": _s(it.get("url")), "application_url": _s(it.get("url")), "posted": to_iso_date(it.get("created_at")),
                    "salary": None, "employment": it.get("job_types"), "remote": True if it.get("remote") is True else None,
                    "tags": [t for t in (it.get("tags") or []) if isinstance(t, str)],
                }, page
            page += 1


class Remotive(Source):
    name = "remotive"
    api_url = "https://remotive.com/api/remote-jobs"
    terms_url = "https://github.com/remotive-com/remote-jobs-api"
    min_interval = 5.0  # provider asks for infrequent calls; this source makes a single request

    def fetch(self, http, cursor):
        data = http.get_json(self.api_url, {"category": "software-dev"})
        for it in (data.get("jobs") if isinstance(data, dict) else None) or []:
            yield {
                "source_id": _s(it.get("id")), "title": _s(it.get("title")), "company": _s(it.get("company_name")),
                "location": _s(it.get("candidate_required_location")) or "Remote", "description_html": it.get("description") or "",
                "url": _s(it.get("url")), "application_url": _s(it.get("url")), "posted": to_iso_date(it.get("publication_date")),
                "salary": _s(it.get("salary")) or None, "employment": it.get("job_type"), "remote": True,
                "tags": [t for t in (it.get("tags") or []) if isinstance(t, str)],
            }, None


class RemoteOK(Source):
    name = "remoteok"
    api_url = "https://remoteok.com/api"
    terms_url = "https://remoteok.com/api"  # requires linking back to the source when displaying
    min_interval = 5.0

    def fetch(self, http, cursor):
        data = http.get_json(self.api_url)
        for it in data if isinstance(data, list) else []:
            if not isinstance(it, dict) or "position" not in it:  # first element is a legal notice
                continue
            lo, hi = it.get("salary_min"), it.get("salary_max")
            yield {
                "source_id": _s(it.get("id")), "title": _s(it.get("position")), "company": _s(it.get("company")),
                "location": _s(it.get("location")) or "Remote", "description_html": it.get("description") or "",
                "url": _s(it.get("url")), "application_url": _s(it.get("apply_url")) or _s(it.get("url")),
                "posted": to_iso_date(it.get("date") or it.get("epoch")),
                "salary": f"${lo}-${hi}" if lo and hi else None, "employment": None, "remote": True,
                "tags": [t for t in (it.get("tags") or []) if isinstance(t, str)],
            }, None


class Jobicy(Source):
    name = "jobicy"
    api_url = "https://jobicy.com/api/v2/remote-jobs"
    terms_url = "https://jobicy.com/jobs-rss-feed"
    min_interval = 5.0

    def fetch(self, http, cursor):
        data = http.get_json(self.api_url, {"count": 50})
        for it in (data.get("jobs") if isinstance(data, dict) else None) or []:
            lo, hi, cur = it.get("salaryMin"), it.get("salaryMax"), it.get("salaryCurrency") or ""
            yield {
                "source_id": _s(it.get("id")), "title": _s(it.get("jobTitle")), "company": _s(it.get("companyName")),
                "location": _s(it.get("jobGeo")) or "Remote",
                "description_html": it.get("jobDescription") or it.get("jobExcerpt") or "",
                "url": _s(it.get("url")), "application_url": _s(it.get("url")), "posted": to_iso_date(it.get("pubDate")),
                "salary": f"{cur}{lo}-{hi}" if lo and hi else None, "employment": it.get("jobType"), "remote": True,
                "tags": [t for t in (it.get("jobIndustry") or []) if isinstance(t, str)],
            }, None


class Adzuna(Source):
    """Optional. Free API key required (https://developer.adzuna.com). Good for India (country=in).
    Adzuna returns truncated descriptions; they are stored as-is and flagged in the quality report."""
    name = "adzuna"
    terms_url = "https://developer.adzuna.com/overview"
    min_interval = 1.5
    needs_key = True

    def __init__(self, country: str = "in", what: str = "software developer"):
        self.country, self.what = country, what
        self.app_id, self.app_key = os.getenv("ADZUNA_APP_ID", ""), os.getenv("ADZUNA_APP_KEY", "")

    def available(self):
        return (bool(self.app_id and self.app_key), "set ADZUNA_APP_ID and ADZUNA_APP_KEY to enable")

    def fetch(self, http, cursor):
        page = int(cursor or 1)
        while page <= 40:
            url = f"https://api.adzuna.com/v1/api/jobs/{self.country}/search/{page}"
            data = http.get_json(url, {"app_id": self.app_id, "app_key": self.app_key, "results_per_page": 50,
                                       "what": self.what, "content-type": "application/json"})
            items = data.get("results") if isinstance(data, dict) else None
            if not items:
                return
            for it in items:
                loc = (it.get("location") or {}).get("display_name") or ""
                lo, hi = it.get("salary_min"), it.get("salary_max")
                yield {
                    "source_id": _s(it.get("id")), "title": _s(it.get("title")),
                    "company": _s((it.get("company") or {}).get("display_name")), "location": loc,
                    "description_html": it.get("description") or "", "url": _s(it.get("redirect_url")),
                    "application_url": _s(it.get("redirect_url")), "posted": to_iso_date(it.get("created")),
                    "salary": f"{int(lo)}-{int(hi)}" if lo and hi else None,
                    "employment": it.get("contract_time"), "remote": None, "tags": [],
                }, page
            page += 1


def all_sources(adzuna_country: str = "in", adzuna_what: str = "software developer") -> dict[str, Source]:
    srcs = [Adzuna(adzuna_country, adzuna_what), Arbeitnow(), Remotive(), RemoteOK(), Jobicy()]
    return {s.name: s for s in srcs}
