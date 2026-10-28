"""Deterministic intent router. Critical operations (apply, list applications, profile edits,
reference resolution) are routed by rules, never by an LLM."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.skills import SKILLS, extract_skills, normalize_skill
from .job_store import JobFilters

ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
            "eighth": 8, "ninth": 9, "tenth": 10}

ALLOWED_INTENTS = {
    "help", "greeting", "show_profile", "confirm_profile", "edit_profile", "recommend", "search",
    "explain_match", "job_details", "apply", "confirm_apply", "cancel", "mark_applied",
    "list_applications", "application_status", "count_applications", "clear_filters", "more", "unknown",
}


@dataclass
class Intent:
    name: str
    ref: str | int | None = None            # "3", "last", or a job id
    filters: JobFilters | None = None
    fresh_search: bool = False
    edits: dict = field(default_factory=dict)


_NUM = r"(?:#|no\.?\s*|number\s*)?(\d{1,4})"
_REF_RE = re.compile(rf"\b(?:job|role|listing|position|result|number|no\.?|#)\s*{_NUM}\b|(?<![\w.])#(\d{{1,4}})\b", re.I)
_ORD_RE = re.compile(r"\b(?:the\s+)?(" + "|".join(ORDINALS) + r"|last)\s+(?:one|job|role|result|listing)\b", re.I)
_ID_RE = re.compile(r"\bj_[a-z0-9]{4,32}\b", re.I)


def extract_ref(msg: str) -> str | int | None:
    if m := _ID_RE.search(msg):
        return m.group(0).lower()
    if m := _REF_RE.search(msg):
        return int(m.group(1) or m.group(2))
    if m := _ORD_RE.search(msg):
        w = m.group(1).lower()
        return "last" if w == "last" else ORDINALS[w]
    if m := re.search(r"\b(?:apply|explain|why|details?|tell me more)\b.*?\b(\d{1,3})\b", msg, re.I):
        return int(m.group(1))
    return None


def parse_filters(msg: str) -> JobFilters:
    low = msg.lower()
    f = JobFilters()
    if re.search(r"\b(remote|work from home|wfh)\b", low) and not re.search(r"\bnot remote\b", low):
        f.remote = "remote"
    elif re.search(r"\bhybrid\b", low):
        f.remote = "hybrid"
    elif re.search(r"\b(on-?site|in[- ]office)\b", low):
        f.remote = "onsite"
    if re.search(r"\b(entry[- ]level|fresher|freshers|junior|graduate|no experience|beginner)\b", low):
        f.level = "entry"
    if m := re.search(r"\b(?:at least|min(?:imum)?|>=?|over|above)?\s*(\d{1,3})\s*%\s*(?:match|or more|\+)?", low):
        f.min_score = min(100, int(m.group(1)))
    if m := re.search(r"(?:less than|under|at most|max(?:imum)?|up to|within)\s*(\d{1,2})\s*(?:\+?\s*)?(?:years?|yrs?)", low):
        f.max_experience_years = float(m.group(1))
    if re.search(r"\bpart[- ]time\b", low):
        f.employment_type = "part_time"
    elif re.search(r"\bfull[- ]time\b", low):
        f.employment_type = "full_time"
    elif re.search(r"\bcontract(?:or)?\b", low):
        f.employment_type = "contract"
    elif re.search(r"\binternships?\b", low):
        f.employment_type = "internship"
    if m := re.search(r"\b(?:in|near|around|based in|located in)\s+([A-Z][A-Za-z.]+(?:[ ,]+[A-Z][A-Za-z.]+){0,2})", msg):
        loc = m.group(1).strip(" ,")
        if loc.lower() not in {"my", "the"} and not extract_skills(loc):
            f.locations = [loc]
    # skills the user is asking for
    f.skills = extract_skills(msg)
    # role words: "python developer jobs", "data analyst roles"
    if m := re.search(r"\b(?:find|search(?: for)?|look(?:ing)? for|show(?: me)?|get me|any)\s+(?:me\s+)?(?:some\s+)?([a-z+#.\- ]{3,40}?)\s+(?:jobs?|roles?|positions?|openings?|vacancies)\b", low):
        role = re.sub(r"\b(remote|entry[- ]level|junior|fresher|full[- ]time|part[- ]time|hybrid|on-?site|the|new|latest|more|all|some|any|available|open|suitable|relevant|good|best|matching|other|different)\b", " ", m.group(1))
        role = re.sub(r"\s+", " ", role).strip()
        if role and role not in {"my", "me"}:
            f.query = role
    return f


def parse_edits(msg: str) -> dict:
    """Profile corrections expressed in chat. Returns a dict of operations."""
    edits: dict = {}
    low = msg.strip()
    if m := re.search(r"\b(?:add|include)\s+(?:the\s+)?skills?\s*[:\-]?\s*(.+)$", low, re.I):
        edits["add_skills"] = [normalize_skill(s) for s in re.split(r",|\band\b", m.group(1)) if s.strip()][:15]
    if m := re.search(r"\b(?:remove|delete|drop)\s+(?:the\s+)?skills?\s*[:\-]?\s*(.+)$", low, re.I):
        edits["remove_skills"] = [normalize_skill(s) for s in re.split(r",|\band\b", m.group(1)) if s.strip()][:15]
    if m := re.search(r"\b(?:set\s+)?(?:my\s+)?(?:experience|years of experience)\s*(?:to|is|=|:)\s*(\d+(?:\.\d+)?)", low, re.I) or \
            re.search(r"\bi (?:have|got)\s+(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)(?:\s+of)?(?:\s+experience)?", low, re.I):
        edits["years_experience"] = float(m.group(1))
    if re.search(r"\b(?:i am|i'm|im)\s+a?\s*(?:fresher|fresh graduate|student)\b", low, re.I):
        edits["years_experience"] = 0.0
    if m := re.search(r"\b(?:my\s+)?(?:designation|target role|role|title|job title)\s*(?:is|to|=|:)\s*(.{3,60})$", low, re.I):
        edits["designation"] = m.group(1).strip(" .")
    if m := re.search(r"\b(?:my\s+)?name\s*(?:is|to|=|:)\s*([A-Za-z][A-Za-z .'-]{1,60})$", low, re.I):
        edits["name"] = m.group(1).strip(" .")
    if m := re.search(r"\b(?:i\s+)?(?:prefer|want|looking for|only want)\s+(remote|hybrid|on-?site)\b", low, re.I):
        edits["work_arrangement"] = m.group(1).lower().replace("-", "")
    if m := re.search(r"\b(?:my\s+)?(?:preferred\s+)?location(?:s)?\s*(?:is|are|to|=|:)\s*(.{2,60})$", low, re.I) or \
            re.search(r"\bi\s+(?:prefer|want to work in|want jobs in)\s+([A-Z][A-Za-z .,]{2,50})$", low):
        edits["preferred_locations"] = [p.strip(" .") for p in re.split(r",|\band\b", m.group(1)) if p.strip()][:5]
    return edits


def parse_intent(msg: str, has_pending_apply: bool, has_profile: bool) -> Intent:
    text = msg.strip()
    low = text.lower()

    if has_pending_apply:
        if re.fullmatch(r"(?:yes|y|yeah|yep|sure|ok(?:ay)?|confirm|go ahead|do it|apply|please do|yes,? apply)[.! ]*", low):
            return Intent("confirm_apply")
        if re.fullmatch(r"(?:no|n|nope|cancel|never ?mind|stop|don'?t|not now)[.! ]*", low):
            return Intent("cancel")
    if re.fullmatch(r"(?:cancel|never ?mind)[.! ]*", low):
        return Intent("cancel")

    if re.fullmatch(r"(?:hi|hello|hey|hii|good (?:morning|afternoon|evening))[.! ]*", low):
        return Intent("greeting")
    if re.search(r"\b(help|what can you do|how (?:do|does) (?:this|it) work|commands)\b", low):
        return Intent("help")

    # applications
    if re.search(r"\bstatus\b", low) and not re.search(r"\bapply to\b", low):
        return Intent("application_status", ref=extract_ref(text))
    if re.search(r"\bhow many\b.*\b(?:jobs?|applications?)\b.*\b(?:appl|submitted|saved)|\bhow many applications\b|\bcount (?:of )?(?:my )?applications\b", low):
        return Intent("count_applications")
    if re.search(r"\b(?:mark|set|update)\b.*\b(?:job\s*)?(?:#?\d+|j_[a-z0-9]+)\b.*\b(?:as\s+)?applied\b|\bi (?:have )?(?:already )?applied (?:to|for|on)\b.*\b(?:job|j_|#|\d)", low):
        return Intent("mark_applied", ref=extract_ref(text))
    if re.search(r"\b(?:my|applied|application|applications)\b.*\b(?:status|history|jobs|applications?|applied)\b|\bshow (?:me )?(?:my )?applied\b|\bwhat (?:have i|did i) appl", low) and not re.search(r"\bapply to\b", low):
        if re.search(r"\bstatus\b", low) and extract_ref(text) is not None:
            return Intent("application_status", ref=extract_ref(text))
        if re.search(r"\bstatus\b", low):
            return Intent("application_status")
        return Intent("list_applications")
    if re.search(r"\b(?:apply|submit(?: an)? application|send (?:an )?application)\b", low):
        return Intent("apply", ref=extract_ref(text))

    # explanations / details
    if re.search(r"\b(?:why|explain|reason|how does|how well)\b", low) and extract_ref(text) is not None:
        return Intent("explain_match", ref=extract_ref(text))
    if re.search(r"\b(?:details?|more (?:info|about)|tell me (?:more )?about|describe|open|view)\b", low) and extract_ref(text) is not None:
        return Intent("job_details", ref=extract_ref(text))

    # profile
    if re.search(r"\b(?:show|view|see|what(?:'s| is))\b.*\b(?:my )?(?:profile|resume details|extracted|parsed)\b", low):
        return Intent("show_profile")
    if re.fullmatch(r"(?:looks? good|that'?s (?:right|correct)|confirm(?: profile)?|correct|all good|yes,? (?:it'?s )?(?:correct|right)|profile (?:is )?(?:correct|ok(?:ay)?|good))[.! ]*", low):
        return Intent("confirm_profile")
    edits = parse_edits(text)
    if edits and not re.match(r"\s*(?:find|search|show|get)\b", low):
        return Intent("edit_profile", edits=edits)

    if re.fullmatch(r"(?:show |give me |see )?(?:more|next|next page|more (?:jobs|results|roles))[.! ]*", low):
        return Intent("more")
    if re.search(r"\b(?:clear|reset|remove)\b.*\bfilters?\b|\bstart over\b|\bshow (?:me )?everything\b", low):
        return Intent("clear_filters")

    # search / recommend
    f = parse_filters(text)
    fresh = bool(re.match(r"\s*(?:please\s+)?(?:find|search|look for|get me|recommend|suggest)\b", low))
    if re.search(r"\b(?:recommend|suggest|best (?:jobs?|matches?)|top (?:jobs?|matches?)|jobs? (?:for me|suitable|that match)|matching jobs?|suitable)\b", low) and f.is_empty():
        return Intent("recommend", filters=f, fresh_search=True)
    if not f.is_empty() or re.search(r"\b(?:jobs?|roles?|positions?|openings?|vacancies)\b", low):
        return Intent("search", filters=f, fresh_search=fresh or bool(f.query))
    if re.search(r"\b(?:only|just|also|filter|narrow|refine)\b", low) and not f.is_empty():
        return Intent("search", filters=f)
    return Intent("unknown")
