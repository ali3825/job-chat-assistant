"""Orchestrator: routes a chat message to a tool and composes the reply.

Flow per message:  parse_intent (rules) -> [LLM classify only if rules said "unknown"] ->
validated tool call -> structured ChatResponse (cards/profile/applications come from tool
results, never from LLM text).

Tools (plain functions, each validating its inputs):
  tool_search_jobs, tool_match, tool_prepare_apply, tool_confirm_apply, tool_list_applications,
  tool_mark_applied, tool_update_profile
"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy.orm import Session as DbSession

from ..core.skills import normalize_skill
from ..errors import AppError
from ..models import STATUS_APPLIED_BY_USER, STATUS_EXTERNAL_REQUIRED
from ..schemas import ApplicationOut, ChatResponse, JobCard, MatchResult, PendingAction, Profile, ProfileUpdate
from . import applications as apps
from .intents import ALLOWED_INTENTS, Intent, parse_intent
from .job_store import JobFilters, JobStore
from .llm import LLMProvider, LLMUnavailable
from .matcher import match_job
from .sessions import Session, SessionStore

log = logging.getLogger("app.orchestrator")
PAGE_SIZE = 10

STATUS_LABEL = {
    STATUS_EXTERNAL_REQUIRED: "Saved — you still need to apply on the employer's page",
    STATUS_APPLIED_BY_USER: "Applied (you confirmed you completed it on the employer's page)",
}

BASE_SUGGESTIONS = ["Show me remote jobs", "Find entry-level jobs suitable for my resume",
                    "Show jobs with at least 80% match", "Show my applied jobs"]


class Orchestrator:
    def __init__(self, store: JobStore, sessions: SessionStore, llm: LLMProvider | None):
        self.store, self.sessions, self.llm = store, sessions, llm

    # ------------------------------------------------------------------ public entry points
    def handle_message(self, db: DbSession, client_id: str, message: str) -> ChatResponse:
        sess = self.sessions.get(client_id)
        intent = parse_intent(message, sess.pending_apply_job_id is not None, sess.profile is not None)
        used_llm = False
        if intent.name == "unknown" and self.llm:
            intent, used_llm = self._llm_route(message, intent)
        if intent.name not in {"apply", "confirm_apply", "cancel"} and sess.pending_apply_job_id and intent.name != "unknown":
            sess.pending_apply_job_id = None  # user moved on; drop the pending confirmation
        handler = getattr(self, f"_h_{intent.name}", self._h_unknown)
        resp = handler(db, client_id, sess, intent, message)
        if used_llm and resp.mode != "llm":
            resp.mode = "llm"
        resp.filters = self._filters_dict(sess.filters)
        if not resp.suggestions:
            resp.suggestions = BASE_SUGGESTIONS if sess.profile else ["Upload your resume (paperclip)", "Find Python developer jobs", "Show me remote jobs"]
        return resp

    def after_resume_parsed(self, client_id: str, profile: Profile, warnings: list[str]) -> ChatResponse:
        sess = self.sessions.get(client_id)
        sess.profile, sess.pending_apply_job_id = profile, None
        sess.last_job_ids, sess.filters, sess.offset = [], JobFilters(), 0
        sess.log("resume_uploaded")
        lines = ["I've read your resume. Here's what I extracted — please review it."]
        inferred = [k for k, v in profile.provenance.items() if v == "inferred"]
        if inferred:
            lines.append("Fields I had to infer (double-check these): " + ", ".join(k.replace("_", " ") for k in inferred) + ".")
        missing = profile.missing_fields()
        if missing:
            lines.append("I couldn't find: " + ", ".join(missing) + ". You can tell me, e.g. “add skill Docker” or “I have 2 years of experience”.")
        lines += [f"Note: {w}" for w in warnings]
        lines.append("If it looks right, say “looks good” and I'll find matching jobs. To fix something, just tell me (or use Edit).")
        return ChatResponse(reply="\n".join(lines), profile=profile,
                            suggestions=["Looks good", "Add skill Docker", "I prefer remote", "Show my profile"])

    def update_profile(self, client_id: str, upd: ProfileUpdate) -> ChatResponse:
        sess = self.sessions.get(client_id)
        if sess.profile is None:
            raise AppError(409, "no_profile", "There's no resume profile to edit yet.", "Upload your resume first.")
        changed = self.tool_update_profile(sess, upd)
        txt = "Updated: " + ", ".join(changed) + "." if changed else "No changes made."
        return ChatResponse(reply=txt, profile=sess.profile)

    # ------------------------------------------------------------------ tools
    def tool_update_profile(self, sess: Session, upd: ProfileUpdate) -> list[str]:
        p, changed = sess.profile, []
        assert p is not None
        data = upd.model_dump(exclude_unset=True)
        for k, v in data.items():
            if k == "confirmed":
                p.confirmed = bool(v); changed.append("confirmed")
                continue
            if k == "skills" and v is not None:
                v = list(dict.fromkeys(normalize_skill(s) for s in v if s.strip()))
            if getattr(p, k) != v:
                setattr(p, k, v)
                p.provenance[k] = "user"
                changed.append(k.replace("_", " "))
        if changed:
            sess.log("profile_edited")
        return changed

    def tool_search_jobs(self, db: DbSession, client_id: str, sess: Session, filters: JobFilters,
                         offset: int = 0) -> tuple[list[JobCard], int]:
        pairs = self.store.search(filters)
        profile = sess.profile
        scored: list[tuple[object, float, MatchResult | None]] = []
        for job, rel in pairs:
            m = match_job(profile, job) if profile else None
            if filters.min_score and (m is None or m.score < filters.min_score):
                continue
            scored.append((job, rel, m))
        scored.sort(key=lambda t: (-(t[2].score if t[2] else 0), -t[1]) if profile else (-t[1],))
        total = len(scored)
        status = apps.applied_status_map(db, client_id)
        page = scored[offset: offset + PAGE_SIZE]
        cards = [JobCard(rank=offset + i + 1, job=j, match=m, applied=j.job_id in status,
                         application_status=status.get(j.job_id)) for i, (j, _, m) in enumerate(page)]
        sess.last_job_ids = [c.job.job_id for c in cards] if offset == 0 else sess.last_job_ids + [c.job.job_id for c in cards]
        sess.last_total = total
        return cards, total

    def tool_match(self, sess: Session, job_id: str) -> MatchResult:
        if sess.profile is None:
            raise AppError(409, "no_profile", "I need your profile to compute a match.", "Upload your resume first.")
        job = self.store.get(job_id)
        if job is None:
            raise AppError(404, "job_not_found", f"No job with ID “{job_id}”.", "Ask me to show jobs again.")
        return match_job(sess.profile, job)

    # ------------------------------------------------------------------ reference resolution
    def resolve_ref(self, sess: Session, ref) -> tuple[str | None, str | None]:
        """Return (job_id, None) or (None, clarification_message)."""
        if isinstance(ref, str) and ref.startswith("j_"):
            return (ref, None) if self.store.get(ref) else (None, f"I can't find a job with ID {ref}. Ask me to show jobs and pick from the list.")
        if ref is None:
            if len(sess.last_job_ids) == 1:
                return sess.last_job_ids[0], None
            if not sess.last_job_ids:
                return None, "I haven't shown you any jobs yet. Ask me to find some first (e.g. “Show me remote jobs”)."
            return None, f"Which job do you mean? I've listed {len(sess.last_job_ids)} — say e.g. “job 2”."
        n = len(sess.last_job_ids) if ref != "last" else 0
        if ref == "last":
            return (sess.last_job_ids[-1], None) if sess.last_job_ids else (None, "I haven't shown you any jobs yet.")
        if not sess.last_job_ids:
            return None, "I haven't shown you any jobs yet. Ask me to find some first (e.g. “Show me remote jobs”)."
        if not (1 <= int(ref) <= n):
            return None, f"There's no job {ref} in my latest list — it has {n} job{'s' if n != 1 else ''} (numbered 1–{n})."
        return sess.last_job_ids[int(ref) - 1], None

    # ------------------------------------------------------------------ intent handlers
    def _h_greeting(self, db, cid, sess, intent, msg):
        return ChatResponse(reply="Hi! I'm your job-search assistant. Upload your resume (PDF or DOCX) with the paperclip and I'll "
                                  "extract your details, recommend matching jobs, and track your applications. "
                                  "You can also just ask, e.g. “Show me remote jobs”.")

    def _h_help(self, db, cid, sess, intent, msg):
        return ChatResponse(reply=(
            "Here's what I can do:\n"
            "• Upload a PDF/DOCX resume and I'll extract your profile (you can correct it)\n"
            "• Recommend and filter jobs: “remote jobs”, “jobs needing React”, “entry-level jobs”, “at least 80% match”, “jobs in Pune”\n"
            "• Explain a match: “why does job 3 match my profile?”\n"
            "• Save an application: “apply to job 3” (I'll ask you to confirm)\n"
            "• Track applications: “show my applied jobs”, “how many jobs have I applied to?”\n"
            "Note: I record applications here and link you to the employer's page — I can't submit to employers for you."))

    def _h_show_profile(self, db, cid, sess, intent, msg):
        if not sess.profile:
            return ChatResponse(reply="I don't have a resume profile yet. Upload a PDF or DOCX resume with the paperclip.")
        miss = sess.profile.missing_fields()
        return ChatResponse(reply="Here's your current profile." + (f" Still missing: {', '.join(miss)}." if miss else ""),
                            profile=sess.profile, suggestions=["Looks good", "Add skill Docker", "I have 3 years of experience"])

    def _h_edit_profile(self, db, cid, sess, intent, msg):
        if not sess.profile:
            return ChatResponse(reply="Upload your resume first so I have a profile to update — or tell me your skills after uploading.")
        e = intent.edits
        p = sess.profile
        upd: dict = {}
        if "add_skills" in e: upd["skills"] = list(dict.fromkeys(p.skills + e["add_skills"]))
        if "remove_skills" in e: upd["skills"] = [s for s in (upd.get("skills") or p.skills) if s not in e["remove_skills"]]
        for k in ("years_experience", "designation", "name", "work_arrangement", "preferred_locations"):
            if k in e: upd[k] = e[k]
        try:
            changed = self.tool_update_profile(sess, ProfileUpdate.model_validate(upd))
        except Exception:
            return ChatResponse(reply="I couldn't apply that change — please check the value and try again.", profile=p)
        reply = ("Updated " + ", ".join(changed) + ". Say “looks good” to see matches, or keep editing.") if changed else "That's already what's on your profile."
        return ChatResponse(reply=reply, profile=p, suggestions=["Looks good", "Show my profile", "Show me remote jobs"])

    def _h_confirm_profile(self, db, cid, sess, intent, msg):
        if not sess.profile:
            return ChatResponse(reply="There's no profile to confirm yet — upload your resume first.")
        sess.profile.confirmed = True
        sess.log("profile_confirmed")
        sess.filters, sess.offset = JobFilters(), 0
        resp = self._search_response(db, cid, sess, intro="Great — profile confirmed. Here are your best matches")
        resp.profile = sess.profile
        return resp

    def _h_recommend(self, db, cid, sess, intent, msg):
        if not sess.profile:
            return ChatResponse(reply="To recommend jobs for you I need your resume. Upload a PDF or DOCX with the paperclip — or ask me to search, e.g. “Find Python developer jobs”.")
        sess.filters, sess.offset = JobFilters(), 0
        return self._search_response(db, cid, sess, intro="Here are your best matches")

    def _h_search(self, db, cid, sess, intent, msg):
        new = intent.filters or JobFilters()
        if new.min_score and not sess.profile:
            return ChatResponse(reply="Match percentages need your resume — upload a PDF or DOCX first and I'll score every job against it.")
        sess.filters = new if intent.fresh_search else self._merge(sess.filters, new)
        sess.offset = 0
        if intent.fresh_search and sess.profile is None and sess.filters.level == "entry":
            pass
        return self._search_response(db, cid, sess, intro="Here's what I found")

    def _h_more(self, db, cid, sess, intent, msg):
        if not sess.last_job_ids:
            return ChatResponse(reply="There's no previous search to continue. Try “Show me remote jobs”.")
        sess.offset += PAGE_SIZE
        if sess.offset >= sess.last_total:
            sess.offset -= PAGE_SIZE
            return ChatResponse(reply="That's everything for this search. Try refining or clearing filters.")
        return self._search_response(db, cid, sess, intro="More results", offset=sess.offset)

    def _h_clear_filters(self, db, cid, sess, intent, msg):
        sess.filters, sess.offset = JobFilters(), 0
        return self._search_response(db, cid, sess, intro="Filters cleared")

    def _h_explain_match(self, db, cid, sess, intent, msg):
        job_id, ask = self.resolve_ref(sess, intent.ref)
        if ask:
            return ChatResponse(reply=ask)
        if not sess.profile:
            return ChatResponse(reply="I need your resume profile to explain a match. Upload it first.")
        m = self.tool_match(sess, job_id)
        job = self.store.get(job_id)
        rank = sess.last_job_ids.index(job_id) + 1 if job_id in sess.last_job_ids else 0
        lines = [f"**{job.title}** at {job.company} — match score {m.score}/100 (confidence: {m.confidence}).", m.explanation, "", "How the score was built:"]
        for c in m.components:
            lines.append(f"• {c.name.title()} (weight {c.weight}): " + (f"{round(c.score * 100)}% — " if c.score is not None else "") + c.note)
        if m.uncertainties:
            lines += ["", "Uncertain: " + " ".join(m.uncertainties)]
        lines += ["", "This is a heuristic fit indicator, not a prediction of hiring outcome."]
        status = apps.applied_status_map(db, cid)
        card = JobCard(rank=rank or 1, job=job, match=m, applied=job_id in status, application_status=status.get(job_id))
        return ChatResponse(reply="\n".join(lines), jobs=[card], suggestions=[f"Apply to job {rank}" if rank else "Show me remote jobs", "Show jobs with at least 80% match"])

    def _h_job_details(self, db, cid, sess, intent, msg):
        job_id, ask = self.resolve_ref(sess, intent.ref)
        if ask:
            return ChatResponse(reply=ask)
        job = self.store.get(job_id)
        m = match_job(sess.profile, job) if sess.profile else None
        rank = sess.last_job_ids.index(job_id) + 1 if job_id in sess.last_job_ids else 1
        status = apps.applied_status_map(db, cid)
        snippet = re.sub(r"\s+", " ", job.description)[:600]
        return ChatResponse(reply=f"**{job.title}** at {job.company} ({job.location}).\n{snippet}{'…' if len(job.description) > 600 else ''}",
                            jobs=[JobCard(rank=rank, job=job, match=m, applied=job_id in status, application_status=status.get(job_id))])

    # ---- applications
    def _h_apply(self, db, cid, sess, intent, msg):
        job_id, ask = self.resolve_ref(sess, intent.ref)
        if ask:
            return ChatResponse(reply=ask)
        job = self.store.get(job_id)
        existing = apps.get_application_for_job(db, cid, job_id)
        m = match_job(sess.profile, job) if sess.profile else None
        rank = sess.last_job_ids.index(job_id) + 1 if job_id in sess.last_job_ids else 1
        card = JobCard(rank=rank, job=job, match=m, applied=existing is not None, application_status=existing.status if existing else None)
        if existing:
            return ChatResponse(reply=f"You already have an application for **{job.title}** at {job.company} (recorded {existing.applied_at:%d %b %Y}). "
                                      f"Status: {STATUS_LABEL.get(existing.status, existing.status)}. I haven't created a duplicate.",
                                jobs=[card], applications=[ApplicationOut.model_validate(existing)])
        sess.pending_apply_job_id = job_id
        return ChatResponse(
            reply=(f"You asked to apply to job {rank}: **{job.title}** at {job.company} ({job.location}).\n"
                   "If you confirm, I'll save this application here and give you the employer's page to complete it — "
                   "I can't submit it to the employer for you. Confirm?"),
            jobs=[card], pending_action=PendingAction(type="confirm_apply", job_id=job_id), suggestions=["Yes, apply", "Cancel"])

    def _h_confirm_apply(self, db, cid, sess, intent, msg):
        job_id = sess.pending_apply_job_id
        sess.pending_apply_job_id = None
        if not job_id:
            return ChatResponse(reply="There's nothing waiting for confirmation. Say “apply to job N” first.")
        return self.tool_confirm_apply(db, cid, sess, job_id)

    def tool_confirm_apply(self, db: DbSession, cid: str, sess: Session, job_id: str) -> ChatResponse:
        res = apps.apply_to_job(db, self.store, cid, job_id)
        a, job = res.application, self.store.get(job_id)
        m = match_job(sess.profile, job) if sess.profile else None
        rank = sess.last_job_ids.index(job_id) + 1 if job_id in sess.last_job_ids else 1
        card = JobCard(rank=rank, job=job, match=m, applied=True, application_status=a.status)
        link = a.application_url or a.source_url
        if res.created:
            sess.log(f"saved:{job_id}")
            reply = (f"Saved ✅ — **{a.job_title}** at {a.company} is recorded in your applications (status: {a.status.replace('_', ' ')}).\n"
                     f"This has NOT been submitted to the employer. To actually apply, open the official page: {link}\n"
                     "After you apply there, tell me “mark job N as applied” to update the status.")
        else:
            reply = f"You'd already saved this one on {a.applied_at:%d %b %Y}, so I didn't create a duplicate. Status: {STATUS_LABEL.get(a.status, a.status)}."
        return ChatResponse(reply=reply, jobs=[card], applications=[ApplicationOut.model_validate(a)],
                            suggestions=["Show my applied jobs", "Show me remote jobs"])

    def _h_cancel(self, db, cid, sess, intent, msg):
        was = sess.pending_apply_job_id
        sess.pending_apply_job_id = None
        return ChatResponse(reply="Okay, I won't apply to that one." if was else "Okay — cancelled. What next?")

    def _h_mark_applied(self, db, cid, sess, intent, msg):
        job_id, ask = self.resolve_ref(sess, intent.ref)
        if ask:
            return ChatResponse(reply=ask)
        if apps.get_application_for_job(db, cid, job_id) is None:
            return ChatResponse(reply="You haven't saved an application for that job yet. Say “apply to job N” first, then mark it applied once you've applied on the employer's page.")
        a = apps.mark_applied(db, cid, job_id)
        sess.log(f"applied_by_user:{job_id}")
        return ChatResponse(reply=f"Updated: **{a.job_title}** at {a.company} is now marked “applied (confirmed by you)”. I can't verify this with the employer.",
                            applications=[ApplicationOut.model_validate(a)])

    def _h_list_applications(self, db, cid, sess, intent, msg):
        rows = apps.list_applications(db, cid)
        if not rows:
            return ChatResponse(reply="You haven't saved any applications yet. Say “apply to job N” on any job in my results.")
        return ChatResponse(reply=f"You have {len(rows)} saved application{'s' if len(rows) != 1 else ''}:",
                            applications=[ApplicationOut.model_validate(r) for r in rows])

    def _h_count_applications(self, db, cid, sess, intent, msg):
        n = apps.count_applications(db, cid)
        return ChatResponse(reply=f"You've saved {n} application{'s' if n != 1 else ''}." + (" Ask “show my applied jobs” for the list." if n else ""))

    def _h_application_status(self, db, cid, sess, intent, msg):
        if intent.ref is not None:
            job_id, ask = self.resolve_ref(sess, intent.ref)
            if ask:
                return ChatResponse(reply=ask)
            a = apps.get_application_for_job(db, cid, job_id)
            job = self.store.get(job_id)
            if not a:
                return ChatResponse(reply=f"You haven't applied to **{job.title}** at {job.company} yet.")
            return ChatResponse(reply=f"**{a.job_title}** at {a.company}: {STATUS_LABEL.get(a.status, a.status)} (recorded {a.applied_at:%d %b %Y}).",
                                applications=[ApplicationOut.model_validate(a)])
        rows = apps.list_applications(db, cid)
        if not rows:
            return ChatResponse(reply="You have no applications yet, so there's no status to show.")
        by: dict[str, int] = {}
        for r in rows:
            by[r.status] = by.get(r.status, 0) + 1
        summary = "; ".join(f"{n} × {STATUS_LABEL.get(s, s)}" for s, n in by.items())
        return ChatResponse(reply=f"{len(rows)} application{'s' if len(rows) != 1 else ''}: {summary}.",
                            applications=[ApplicationOut.model_validate(r) for r in rows])

    # ---- fallback / LLM
    def _h_unknown(self, db, cid, sess, intent, msg):
        if self.llm:
            try:
                text = self.llm.complete(
                    "You are a concise job-search assistant inside an app. You can give brief career/job-search advice. "
                    "You have NO ability to search jobs, apply, or read files in this reply, so never claim you did. "
                    "If the user wants an action, tell them to use phrases like 'show me remote jobs', 'apply to job 3', "
                    "or 'show my applied jobs'. Keep it under 120 words.", msg[:1000], max_tokens=250)
                if text:
                    return ChatResponse(reply=text, mode="llm")
            except LLMUnavailable:
                return ChatResponse(reply="My AI service isn't responding right now, so I'm using basic mode. I can still search jobs, explain matches and track applications — try “Show me remote jobs” or say “help”.")
        return ChatResponse(reply="I'm not sure what you mean. I can search jobs (“show me remote jobs”, “jobs needing React”), explain a match (“why job 3?”), "
                                  "save an application (“apply to job 3”), or list your applications (“show my applied jobs”). Say “help” for more.")

    # ------------------------------------------------------------------ helpers
    def _search_response(self, db, cid, sess, intro: str, offset: int = 0) -> ChatResponse:
        if len(self.store) == 0:
            return ChatResponse(reply="The job dataset is empty right now, so I can't search. "
                                      "(Admin: run scripts/collect_jobs.py to build data/jobs.json.)",
                                error={"code": "empty_dataset"})
        cards, total = self.tool_search_jobs(db, cid, sess, sess.filters, offset)
        f = sess.filters
        if not cards:
            if f.min_score and not f.is_empty():
                tip = "Try lowering the match threshold or removing a filter."
            else:
                tip = "Try removing a filter (say “clear filters”) or a broader role."
            return ChatResponse(reply=f"No jobs matched ({f.describe()}). {tip}", suggestions=["Clear filters", "Show me remote jobs"])
        sess.log(f"search:{total}")
        end = offset + len(cards)
        reply = f"{intro} — showing {offset + 1}–{end} of {total}" + (f" (filters: {f.describe()})" if not f.is_empty() else "") + "."
        if sess.profile is None:
            reply += " Upload your resume to get match scores and reasons."
        elif not sess.profile.confirmed:
            reply += " (Based on your profile as parsed — say “looks good” once it's correct.)"
        if total > end:
            reply += " Say “show more” for the next page."
        return ChatResponse(reply=reply, jobs=cards, suggestions=["Apply to job 1", "Why does job 1 match my profile?", "Only remote"] if sess.profile else ["Show me remote jobs"])

    @staticmethod
    def _merge(old: JobFilters, new: JobFilters) -> JobFilters:
        return JobFilters(
            query=new.query or old.query,
            skills=list(dict.fromkeys(old.skills + new.skills)),
            remote=new.remote or old.remote,
            locations=new.locations or old.locations,
            level=new.level or old.level,
            max_experience_years=new.max_experience_years if new.max_experience_years is not None else old.max_experience_years,
            employment_type=new.employment_type or old.employment_type,
            min_score=new.min_score or old.min_score,
        )

    @staticmethod
    def _filters_dict(f: JobFilters) -> dict | None:
        return None if f.is_empty() else {"description": f.describe()}

    def _llm_route(self, message: str, fallback: Intent) -> tuple[Intent, bool]:
        """Ask the LLM to classify a message the rules couldn't. Output is validated against an
        allow-list; the LLM can never trigger confirm_apply/mark_applied, and apply still needs a user 'yes'."""
        system = ("Classify the user's message for a job-search assistant. Reply with JSON only: "
                  '{"intent": one of ' + json.dumps(sorted(ALLOWED_INTENTS - {"confirm_apply", "mark_applied", "edit_profile"})) +
                  ', "ref": integer or null, "skills": [strings], "remote": true|false|null, "query": string or null}')
        try:
            raw = self.llm.complete(system, message[:500], max_tokens=150)
            data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
            name = data.get("intent")
            if name not in ALLOWED_INTENTS or name in {"confirm_apply", "mark_applied", "edit_profile"}:
                return fallback, False
            ref = data.get("ref")
            f = JobFilters(query=data.get("query") if isinstance(data.get("query"), str) else None,
                           skills=[normalize_skill(s) for s in data.get("skills", []) if isinstance(s, str)][:8],
                           remote="remote" if data.get("remote") is True else None)
            return Intent(name, ref=ref if isinstance(ref, int) and 0 < ref < 10000 else None, filters=f, fresh_search=bool(f.query)), True
        except (LLMUnavailable, ValueError, KeyError, TypeError, AttributeError):
            return fallback, False
