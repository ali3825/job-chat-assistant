"""Per-client conversation memory (in-process, TTL-evicted).

Holds: the candidate profile, active filters, the last result list (so "job 3" resolves to the
3rd job shown), a pending apply confirmation and recent actions. Nothing here is persisted:
resumes/profiles are never written to disk. Applications live in the database.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

from ..errors import AppError
from ..schemas import Profile
from .job_store import JobFilters

CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


@dataclass
class Session:
    profile: Profile | None = None
    filters: JobFilters = field(default_factory=JobFilters)
    last_job_ids: list[str] = field(default_factory=list)
    pending_apply_job_id: str | None = None
    offset: int = 0
    last_total: int = 0
    recent_actions: list[str] = field(default_factory=list)
    touched: float = field(default_factory=time.time)

    def log(self, action: str) -> None:
        self.recent_actions = (self.recent_actions + [action])[-10:]


class SessionStore:
    def __init__(self, ttl_minutes: int = 120, max_sessions: int = 2000):
        self._s: dict[str, Session] = {}
        self._lock = threading.Lock()
        self.ttl, self.max = ttl_minutes * 60, max_sessions

    def get(self, client_id: str) -> Session:
        if not CLIENT_ID_RE.match(client_id or ""):
            raise AppError(400, "invalid_client_id", "Missing or invalid X-Client-Id header.",
                           "Reload the page so a new session ID is created.")
        now = time.time()
        with self._lock:
            if len(self._s) >= self.max:
                for k in [k for k, v in self._s.items() if now - v.touched > self.ttl]:
                    del self._s[k]
                if len(self._s) >= self.max:  # still full: drop least recently used
                    del self._s[min(self._s, key=lambda k: self._s[k].touched)]
            sess = self._s.get(client_id)
            if sess is None or now - sess.touched > self.ttl:
                sess = self._s[client_id] = Session()
            sess.touched = now
            return sess
