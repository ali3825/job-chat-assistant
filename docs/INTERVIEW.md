# Interview notes (what the code actually does)
**Overview**: React/Vite chat UI + FastAPI. Resume (PDF/DOCX) -> rule-based parser -> editable Profile -> deterministic matcher over a JSON job dataset -> tracked applications in SQLite/Postgres. LLM is optional.

**Orchestrator**: `services/orchestrator.py`. `intents.parse_intent` (regex rules) picks an intent; handlers call tools (search, match, apply, list, update profile). The LLM is consulted only when rules return `unknown`; its output is validated against an allow-list and can never trigger confirm_apply / mark_applied / edit_profile. Cards and tables come from tool results, never LLM text.

**Matching**: score = 100 * sum(w*s)/sum(w) over evaluable components: skills 50, title 20, experience 15, location 10, education 5. Unknown inputs are excluded (not zero) and listed as uncertainties; confidence is high/medium/low by evaluable weight. Related skills (same family) earn 0.5. Heuristic, not a hiring probability.

**Applications**: UNIQUE(client_id, job_id) + pre-check + IntegrityError handling for races. Status `external_application_required` (saved, not submitted) or `applied_by_user` (user says they did it). Apply needs a confirmation step.

**Sessions**: in-memory per X-Client-Id (random UUID in localStorage), TTL + cap. Holds profile, filters, last result list ("job 3" = 3rd in last list), pending apply. Not authenticated: anyone with the ID can access that session; applications are scoped by the same ID.

**Collector**: `scripts/collect_jobs.py`; sources behind a Source interface; robots check, paced requests, exponential backoff, JSONL checkpoints + state.json (resumable), dedupe by source id / canonical URL / title+company+location, schema validation, quality report.

**Security**: magic-byte + extension checks, size cap, zip-bomb guard, in-memory handling, no resume text in logs, generic 500s, parameterised ORM, CORS allow-list.

**Likely questions**: why rules before LLM? why unique constraint as well as a check? why exclude unknown components? how is "job 3" resolved? what if the LLM is down? why SQLite + disk vs Postgres? how does resume parsing handle scans (OCR via tesseract if installed)? limits of regex parsing? how would you add auth? how would you scale sessions (Redis)? how do you avoid prompt injection (LLM never gets resume text or tool authority)? how is dedupe done? why create_all instead of Alembic? what are the dataset's freshness limits? how would you test the matcher? why httpx not an SDK? how do retries avoid hammering sources? what does `confidence` mean? why a separate `applied_by_user` state? how do you serve the SPA in production? CORS config? what would you improve next (embeddings, streaming, auth, Alembic)?
