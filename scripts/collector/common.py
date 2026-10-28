"""Shared helpers for the job collector: polite HTTP client, robots check, normalisation."""
from __future__ import annotations

import hashlib
import logging
import random
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger("collector")


class SourceUnavailable(Exception):
    """A source could not be reached/parsed after retries; the run continues with other sources."""


class HttpClient:
    """httpx wrapper: identifying User-Agent, per-source minimum interval, retries with
    exponential backoff + jitter, Retry-After support. Never retries 4xx (except 429)."""

    def __init__(self, contact: str, min_interval: float = 1.0, max_retries: int = 5,
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        self.client = httpx.Client(
            timeout=30, follow_redirects=True, transport=transport,
            headers={"User-Agent": f"JobAssistantCollector/1.0 (assessment project; contact: {contact})",
                     "Accept": "application/json"})
        self.min_interval, self.max_retries, self._sleep = min_interval, max_retries, sleep
        self._last = 0.0

    def _pace(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            self._sleep(wait)
        self._last = time.monotonic()

    def get_json(self, url: str, params: dict | None = None):
        last_err = "unknown"
        for attempt in range(self.max_retries + 1):
            self._pace()
            try:
                r = self.client.get(url, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_err = type(exc).__name__
            else:
                if r.status_code == 200:
                    try:
                        return r.json()
                    except ValueError:
                        raise SourceUnavailable(f"{url}: response was not valid JSON")
                if r.status_code == 429 or r.status_code >= 500:
                    last_err = f"HTTP {r.status_code}"
                    ra = r.headers.get("Retry-After", "")
                    if ra.isdigit():
                        self._sleep(min(int(ra), 120))
                else:
                    raise SourceUnavailable(f"{url}: HTTP {r.status_code}")
            if attempt < self.max_retries:
                delay = min(2 ** attempt, 60) + random.random()
                log.warning("retry %d/%d for %s (%s) in %.1fs", attempt + 1, self.max_retries, url, last_err, delay)
                self._sleep(delay)
        raise SourceUnavailable(f"{url}: gave up after {self.max_retries + 1} attempts ({last_err})")

    def robots_allows(self, url: str) -> bool:
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        try:
            r = self.client.get(robots_url, headers={"Accept": "text/plain"})
        except httpx.HTTPError:
            log.warning("could not fetch %s; skipping source to be safe", robots_url)
            return False
        if r.status_code in (404, 410) or 400 <= r.status_code < 500:
            return True  # no robots restrictions published (RFC 9309)
        if r.status_code != 200:
            return False
        rp = RobotFileParser()
        rp.parse(r.text.splitlines())
        return rp.can_fetch(self.client.headers["User-Agent"], url)

    def close(self) -> None:
        self.client.close()


class _Stripper(HTMLParser):
    BLOCK = {"p", "br", "li", "div", "h1", "h2", "h3", "h4", "tr", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n• ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str | None, limit: int = 8000) -> str:
    if not html:
        return ""
    s = _Stripper()
    try:
        s.feed(html)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)[:limit]
    text = "".join(s.parts)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text[:limit]


def canonical_url(url: str) -> str:
    p = urlsplit(url.strip())
    return urlunsplit((p.scheme.lower() or "https", p.netloc.lower(), p.path.rstrip("/"), "", ""))


def make_job_id(source: str, source_id: str) -> str:
    return "j_" + hashlib.sha1(f"{source}:{source_id}".encode()).hexdigest()[:12]


def fingerprint(title: str, company: str, location: str) -> str:
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return hashlib.sha1(f"{norm(title)}|{norm(company)}|{norm(location)}".encode()).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_iso_date(value) -> str | None:
    """Accept epoch seconds/ms or an ISO-ish string; return YYYY-MM-DD or None."""
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            v = float(value)
            v = v / 1000 if v > 1e11 else v
            return datetime.fromtimestamp(v, tz=timezone.utc).strftime("%Y-%m-%d")
        m = re.match(r"(\d{4}-\d{2}-\d{2})", str(value))
        return m.group(1) if m else None
    except (ValueError, OverflowError, OSError):
        return None


_EXP_RANGE = re.compile(r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I)
_EXP_MIN = re.compile(r"(?:minimum|at least|min\.?|over|more than)?\s*(\d{1,2})\s*(?:\+|plus)?\s*(?:years?|yrs?)(?:'|’)?(?:\s+of)?\s+(?:[a-z/&-]+\s+){0,3}?experience", re.I)
_EXP_MIN2 = re.compile(r"experience\s*(?:of|:)?\s*(?:at least|minimum)?\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I)


def parse_experience(title: str, description: str) -> dict:
    """Heuristic experience parsing. Returns experience_required/min/max/level (None when unstated)."""
    text = f"{title}\n{description[:5000]}"
    lo = hi = None
    if m := _EXP_RANGE.search(text):
        a, b = int(m.group(1)), int(m.group(2))
        if a <= b <= 20:
            lo, hi = a, b
    if lo is None:
        for rx in (_EXP_MIN, _EXP_MIN2):
            for m in rx.finditer(text):
                if int(m.group(1)) <= 15:
                    lo = int(m.group(1))
                    break
            if lo is not None:
                break
    low = text.lower()
    level = None
    if re.search(r"\b(senior|sr\.?|lead|principal|staff|head of)\b", title.lower()):
        level = "senior"
    elif re.search(r"\b(junior|jr\.?|entry[- ]level|graduate|fresher|intern(?:ship)?|trainee|apprentice)\b", title.lower()) or \
            re.search(r"\b(entry[- ]level|fresher|no experience (?:required|needed))\b", low):
        level = "entry"
    if lo is None and level == "entry":
        lo = 0
    if lo is not None and level is None:
        level = "entry" if lo <= 1 else "mid" if lo < 5 else "senior"
    if lo is None:
        text_req = "Not specified" if not level else ("Senior level" if level == "senior" else "Entry level")
    elif hi is not None:
        text_req = f"{lo}-{hi} years"
    elif lo == 0:
        text_req = "Entry level"
    else:
        text_req = f"{lo}+ years"
    return {"experience_required": text_req, "min_experience_years": float(lo) if lo is not None else None,
            "max_experience_years": float(hi) if hi is not None else None, "experience_level": level}


def remote_status(remote_flag: bool | None, *texts: str) -> str:
    if remote_flag is True:
        return "remote"
    blob = " ".join(t for t in texts if t).lower()
    if re.search(r"\bhybrid\b", blob):
        return "hybrid"
    if re.search(r"\b(fully remote|100% remote|remote[- ]first|work from home|remote)\b", blob[:600]):
        return "remote"
    if re.search(r"\b(on-?site|in[- ]office)\b", blob):
        return "onsite"
    return "unknown"


def employment_type(value) -> str | None:
    v = " ".join(value) if isinstance(value, list) else str(value or "")
    v = v.lower()
    for key, out in (("intern", "internship"), ("part", "part_time"), ("contract", "contract"),
                     ("freelance", "contract"), ("full", "full_time"), ("permanent", "full_time")):
        if key in v:
            return out
    return None
