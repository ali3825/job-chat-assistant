"""Transparent, deterministic job matcher (see README "Matching algorithm").

score = 100 * sum(weight_i * score_i) / sum(weight_i)   over components that CAN be evaluated.
Components whose inputs are missing (e.g. the resume gives no years of experience, or the job
states no experience requirement) are excluded and reported as uncertainties instead of being
scored as zero. The result is a heuristic fit indicator, NOT a probability of being hired.
"""
from __future__ import annotations

import re

from ..core.skills import related_skills
from ..schemas import ComponentScore, Job, MatchResult, Profile

WEIGHTS = {"skills": 50, "title": 20, "experience": 15, "location": 10, "education": 5}
_STOP = {"senior", "sr", "junior", "jr", "lead", "principal", "staff", "associate", "intern", "trainee",
         "i", "ii", "iii", "the", "a", "an", "of", "and", "for", "remote", "-", "–", "|", "/", "&", "at"}
_SYN = {"developer": "engineer", "programmer": "engineer", "sde": "engineer", "swe": "engineer",
        "dev": "engineer", "frontend": "front-end", "front": "front-end", "backend": "back-end",
        "back": "back-end", "fullstack": "full-stack", "full": "full-stack", "ml": "machine-learning"}
_TOK = re.compile(r"[a-z0-9+#.]+")
_EDU_REQ = [
    (3, re.compile(r"\b(ph\.?d|doctorate)\b", re.I)),
    (2, re.compile(r"\b(master'?s|m\.?\s?tech|m\.?\s?sc|mca|mba)\b", re.I)),
    (1, re.compile(r"\b(bachelor'?s?|b\.?\s?tech|b\.?\s?e\.?|b\.?\s?sc|degree in|bs\b|undergraduate degree)", re.I)),
]
_EDU_NAMES = {1: "bachelor's degree", 2: "master's degree", 3: "doctorate"}


def _title_tokens(title: str) -> set[str]:
    toks = [_SYN.get(t, t) for t in _TOK.findall(title.lower())]
    return {t for t in toks if t not in _STOP and len(t) > 1}


def _job_education_requirement(job: Job) -> int | None:
    text = job.description[:6000]
    if not re.search(r"\b(require[sd]?|must have|degree|qualification|education)\b", text, re.I):
        return None
    for level, rx in _EDU_REQ:
        if rx.search(text):
            return level
    return None


def match_job(profile: Profile, job: Job) -> MatchResult:
    comps: list[ComponentScore] = []
    uncertainties: list[str] = []
    cand_skills = set(profile.skills)
    matched: list[str] = []
    partial: list[str] = []
    missing: list[str] = []

    # ---- skills (50)
    if job.skills and cand_skills:
        credit = 0.0
        for s in job.skills:
            if s in cand_skills:
                matched.append(s); credit += 1
            elif related_skills(s) & cand_skills:
                partial.append(s); credit += 0.5
            else:
                missing.append(s)
        sc = credit / len(job.skills)
        note = f"{len(matched)} of {len(job.skills)} listed skills match" + (f", {len(partial)} related" if partial else "")
        comps.append(ComponentScore(name="skills", weight=WEIGHTS["skills"], score=sc, note=note))
    else:
        reason = "the resume lists no skills" if not cand_skills else "the job lists no recognisable skills"
        comps.append(ComponentScore(name="skills", weight=WEIGHTS["skills"], score=None, note=f"Not evaluated: {reason}"))
        uncertainties.append(f"Skill fit unknown ({reason}).")

    # ---- title (20)
    targets = [profile.designation] if profile.designation else []
    if targets:
        jt = _title_tokens(job.title)
        best = 0.0
        for t in targets:
            ct = _title_tokens(t)
            if jt and ct:
                best = max(best, len(jt & ct) / min(len(jt), len(ct)))
        if best < 0.5 and any(s in job.title.lower() for s in profile.skills[:15]):
            best = max(best, 0.5)
        comps.append(ComponentScore(name="title", weight=WEIGHTS["title"], score=min(best, 1.0),
                                    note=f"“{job.title}” vs your designation “{profile.designation}”"))
    else:
        comps.append(ComponentScore(name="title", weight=WEIGHTS["title"], score=None, note="Not evaluated: no designation on profile"))
        uncertainties.append("Role fit unknown (no designation on the profile).")

    # ---- experience (15)
    jmin, jmax, y = job.min_experience_years, job.max_experience_years, profile.years_experience
    if jmin is None and job.experience_level != "entry":
        comps.append(ComponentScore(name="experience", weight=WEIGHTS["experience"], score=None, note="Not evaluated: job states no experience requirement"))
    elif y is None:
        comps.append(ComponentScore(name="experience", weight=WEIGHTS["experience"], score=None, note="Not evaluated: resume gives no years of experience"))
        uncertainties.append(f"Job asks for {job.experience_required}; your experience isn't on the profile.")
    else:
        need = jmin if jmin is not None else 0.0
        if y >= need:
            sc = 0.7 if (jmax is not None and y > jmax + 2) else 1.0
            note = f"{y:g} yrs vs {job.experience_required}" + (" (possibly over-qualified)" if sc < 1 else "")
        else:
            sc = max(0.0, 1 - (need - y) / 3)
            note = f"{y:g} yrs vs {need:g}+ required"
        comps.append(ComponentScore(name="experience", weight=WEIGHTS["experience"], score=sc, note=note))

    # ---- location / work arrangement (10)
    loc_score, loc_note = _location(profile, job)
    comps.append(ComponentScore(name="location", weight=WEIGHTS["location"], score=loc_score, note=loc_note))
    if loc_score is None and (profile.preferred_locations or profile.work_arrangement):
        uncertainties.append("Location/work-arrangement fit unclear from the listing.")

    # ---- education (5) -- only when the job explicitly states a requirement
    req = _job_education_requirement(job)
    have = profile.education_level
    if req is None:
        comps.append(ComponentScore(name="education", weight=WEIGHTS["education"], score=None, note="Not evaluated: job states no degree requirement"))
    elif have is None:
        comps.append(ComponentScore(name="education", weight=WEIGHTS["education"], score=None, note="Not evaluated: no education on profile"))
        uncertainties.append(f"Job mentions a {_EDU_NAMES[req]}; no education found on the profile.")
    else:
        ok = have >= req
        comps.append(ComponentScore(name="education", weight=WEIGHTS["education"], score=1.0 if ok else 0.0,
                                    note=f"Job mentions a {_EDU_NAMES[req]}; " + ("profile meets it" if ok else "profile may not meet it")))

    known = [c for c in comps if c.score is not None]
    total_w = sum(c.weight for c in known)
    score = round(100 * sum(c.weight * c.score for c in known) / total_w) if total_w else 0
    known_ratio = total_w / sum(WEIGHTS.values())
    confidence = "high" if known_ratio >= 0.8 else "medium" if known_ratio >= 0.5 else "low"
    if confidence == "low":
        uncertainties.append("Score is based on limited information; treat it as a rough guide.")

    return MatchResult(
        score=score, confidence=confidence, matched_skills=matched, partial_skills=partial,
        missing_skills=missing[:8], uncertainties=uncertainties,
        explanation=_explain(job, score, matched, partial, missing, comps), components=comps,
    )


def _location(profile: Profile, job: Job) -> tuple[float | None, str]:
    wa, locs, rs = profile.work_arrangement, profile.preferred_locations, job.remote_status
    if wa == "remote":
        if rs == "remote": return 1.0, "Remote role matches your remote preference"
        if rs == "hybrid": return 0.5, "Hybrid role; you prefer remote"
        if rs == "onsite": return 0.0, "On-site role; you prefer remote"
        return None, "Listing doesn't say whether it's remote"
    if locs:
        jl = job.location.lower()
        if any(l.lower() in jl for l in locs):
            return 1.0, f"Located in {job.location}"
        if rs == "remote":
            return 0.8, "Remote role, so location is flexible"
        if jl in {"", "not specified"}:
            return None, "Listing doesn't state a location"
        return 0.0, f"Located in {job.location}; you prefer {', '.join(locs)}"
    if wa in {"hybrid", "onsite"}:
        if rs == wa: return 1.0, f"{wa.title()} role matches your preference"
        if rs == "unknown": return None, "Listing doesn't state the work arrangement"
        return 0.3 if rs == "remote" else 0.0, f"{rs.title()} role; you prefer {wa}"
    return None, "No location preference set"


def _explain(job: Job, score: int, matched: list[str], partial: list[str], missing: list[str],
             comps: list[ComponentScore]) -> str:
    bits = []
    if matched:
        bits.append("Matches your skills in " + ", ".join(matched[:6]))
    if partial:
        bits.append("related experience for " + ", ".join(partial[:3]))
    by = {c.name: c for c in comps}
    t = by["title"]
    if t.score is not None and t.score >= 0.5:
        bits.append("the title lines up with your designation")
    e = by["experience"]
    if e.score is not None:
        bits.append("your experience fits the requirement" if e.score >= 0.99 else "you're slightly under the experience asked for")
    loc = by["location"]
    if loc.score is not None and loc.score >= 0.8:
        bits.append("the location/work arrangement suits you")
    text = (bits[0] if bits else "Limited overlap found")
    if len(bits) > 1:
        text += "; " + "; ".join(bits[1:])
    text += "."
    if missing:
        text += " Not found on your profile: " + ", ".join(missing[:4]) + "."
    return text
