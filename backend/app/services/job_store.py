"""In-memory job index loaded from the JSON dataset (data/jobs.json).

Read-only at runtime. If the file is missing/empty the app still starts and
reports an empty dataset instead of failing.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from ..schemas import Job

log = logging.getLogger("app.jobs")
_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")
FIXTURE_SOURCE_PREFIX = "TEST_FIXTURE"


@dataclass
class JobFilters:
    query: str | None = None          # role/title keywords, e.g. "python developer"
    skills: list[str] = field(default_factory=list)   # job must list ALL of these
    remote: str | None = None         # remote | hybrid | onsite
    locations: list[str] = field(default_factory=list)
    level: str | None = None          # entry
    max_experience_years: float | None = None
    employment_type: str | None = None
    min_score: int | None = None      # applied by the orchestrator after matching

    def is_empty(self) -> bool:
        return not any([self.query, self.skills, self.remote, self.locations, self.level,
                        self.max_experience_years is not None, self.employment_type, self.min_score])

    def describe(self) -> str:
        parts = []
        if self.query: parts.append(f"role “{self.query}”")
        if self.skills: parts.append("skills: " + ", ".join(self.skills))
        if self.remote: parts.append(self.remote)
        if self.locations: parts.append("location: " + ", ".join(self.locations))
        if self.level: parts.append("entry-level")
        if self.max_experience_years is not None: parts.append(f"≤ {self.max_experience_years:g} yrs experience")
        if self.employment_type: parts.append(self.employment_type)
        if self.min_score: parts.append(f"match ≥ {self.min_score}%")
        return "; ".join(parts) if parts else "none"


class JobStore:
    def __init__(self, jobs: list[Job] | None = None, skipped: int = 0):
        self._jobs: dict[str, Job] = {}
        self._text: dict[str, str] = {}
        self.skipped_invalid = skipped
        for j in jobs or []:
            self._jobs[j.job_id] = j
            self._text[j.job_id] = f"{j.title} {' '.join(j.skills)} {j.company}".lower()

    @classmethod
    def load(cls, path: Path) -> "JobStore":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            log.warning("Job dataset not found at %s", path)
            return cls()
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Could not read job dataset: %s", type(exc).__name__)
            return cls()
        records = raw.get("jobs", []) if isinstance(raw, dict) else raw
        jobs, skipped = [], 0
        for rec in records if isinstance(records, list) else []:
            try:
                jobs.append(Job.model_validate(rec))
            except ValidationError:
                skipped += 1
        if skipped:
            log.warning("Skipped %d invalid job records", skipped)
        return cls(jobs, skipped)

    # ---- basic access
    def __len__(self) -> int:
        return len(self._jobs)

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        return list(self._jobs.values())

    def stats(self) -> dict:
        sources: dict[str, int] = {}
        for j in self._jobs.values():
            sources[j.source_name] = sources.get(j.source_name, 0) + 1
        return {
            "count": len(self._jobs),
            "sources": sources,
            "is_test_fixture": any(s.startswith(FIXTURE_SOURCE_PREFIX) for s in sources),
            "skipped_invalid": self.skipped_invalid,
        }

    # ---- search
    def search(self, f: JobFilters, limit: int | None = None) -> list[tuple[Job, float]]:
        """Return (job, relevance) pairs passing the hard filters, best relevance first.

        Relevance only orders results by text overlap with the query; it says nothing
        about whether the candidate is eligible (that is the matcher's job)."""
        q_tokens = _TOKEN_RE.findall(f.query.lower()) if f.query else []
        out: list[tuple[Job, float]] = []
        for jid, job in self._jobs.items():
            if f.remote and job.remote_status != f.remote:
                continue
            if f.skills and not all(s in job.skills for s in f.skills):
                continue
            if f.locations and not any(loc.lower() in job.location.lower() or
                                       (job.remote_status == "remote" and loc.lower() == "remote")
                                       for loc in f.locations):
                continue
            if f.employment_type and (job.employment_type or "").lower().replace("-", "_") != f.employment_type:
                continue
            if f.level == "entry" and not (job.experience_level == "entry" or
                                           (job.min_experience_years is not None and job.min_experience_years <= 1)):
                continue
            if f.max_experience_years is not None and (job.min_experience_years is None or
                                                       job.min_experience_years > f.max_experience_years):
                continue
            relevance = 0.0
            if q_tokens:
                title_tokens = set(_TOKEN_RE.findall(job.title.lower()))
                skill_tokens = {t for s in job.skills for t in _TOKEN_RE.findall(s)}
                hay = self._text[jid]
                for t in q_tokens:
                    if t in {"jobs", "job", "roles", "role", "developer", "engineer"} and len(q_tokens) > 1:
                        relevance += 1 if t in title_tokens else 0
                    elif t in title_tokens:
                        relevance += 3
                    elif t in skill_tokens:
                        relevance += 2
                    elif t in hay:
                        relevance += 1
                if relevance == 0:
                    continue
            out.append((job, relevance))
        out.sort(key=lambda p: (-p[1], p[0].posted_date or "", p[0].job_id))
        return out[:limit] if limit else out
