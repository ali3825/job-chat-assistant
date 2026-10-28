import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { ApplicationsTable } from "./components/ApplicationsTable";
import { JobCard } from "./components/JobCard";
import { ProfileCard } from "./components/ProfileCard";
import { Rich } from "./components/Rich";
import type { ChatMessage, ChatResponse, Health, Profile } from "./types";

const WELCOME: ChatResponse = {
  reply: "Hi! I'm JobPilot. Upload your resume (PDF or DOCX) and I'll extract your details, recommend matching jobs with reasons, " +
    "and keep track of your applications. You can also just ask me to search.",
  mode: "fallback", profile: null, jobs: null, applications: null, pending_action: null, filters: null,
  suggestions: ["Show me remote jobs", "Find Python developer jobs", "Show my applied jobs"],
};

let nextId = 1;
const mk = (role: ChatMessage["role"], text: string, extra: Partial<ChatMessage> = {}): ChatMessage =>
  ({ id: nextId++, role, text, ...extra });

export default function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([mk("assistant", WELCOME.reply, { data: WELCOME })]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [parsing, setParsing] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [dragging, setDragging] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => { api.health().then(setHealth).catch(() => setHealth(null)); }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages, busy]);

  const push = useCallback((m: ChatMessage) => setMessages((prev) => [...prev, m]), []);

  const send = useCallback(async (payload: string, display?: string) => {
    const text = payload.trim();
    if (!text || busy) return;
    push(mk("user", display ?? text));
    setBusy(true);
    try {
      const data = await api.chat(text);
      push(mk("assistant", data.reply, { data }));
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError("Something went wrong.", "Please try again.");
      push(mk("assistant", "", { error: { message: err.message, recovery: err.recovery, retry: () => void send(payload, display) } }));
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }, [busy, push]);

  const upload = useCallback(async (file: File | undefined | null) => {
    if (!file || busy) return;
    push(mk("user", `📎 ${file.name}`));
    setBusy(true); setUploadPct(0); setParsing(false);
    try {
      const data = await api.uploadResume(file, (p) => { setUploadPct(p); if (p >= 100) setParsing(true); });
      push(mk("assistant", data.reply, { data }));
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError("Upload failed.", "Please try again.");
      push(mk("assistant", "", { error: { message: err.message, recovery: err.recovery, retry: () => fileRef.current?.click() } }));
    } finally {
      setBusy(false); setUploadPct(null); setParsing(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }, [busy, push]);

  const saveProfile = useCallback(async (patch: Partial<Profile>) => {
    try {
      const data = await api.patchProfile(patch);
      push(mk("assistant", data.reply, { data }));
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError("Couldn't save changes.");
      push(mk("assistant", "", { error: { message: err.message, recovery: err.recovery } }));
    }
  }, [push]);

  const last = messages[messages.length - 1];
  const suggestions = last?.role === "assistant" ? last.data?.suggestions ?? [] : [];

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const t = input;
    setInput("");
    void send(t);
  }

  return (
    <div className="app"
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setDragging(false); }}
      onDrop={(e) => { e.preventDefault(); setDragging(false); void upload(e.dataTransfer.files?.[0]); }}>
      <header className="topbar">
        <div className="brand"><span className="logo" aria-hidden>✈</span>
          <div><h1>JobPilot</h1><p>Upload a resume → get matched jobs → track applications</p></div></div>
        <button className="btn ghost" onClick={() => fileRef.current?.click()} disabled={busy}>Upload resume</button>
      </header>

      {health && health.dataset.count === 0 && <div className="banner warn" role="status">The job dataset is empty, so searches return nothing. (Admin: run <code>scripts/collect_jobs.py</code>.)</div>}
      {health?.dataset.is_test_fixture && <div className="banner warn" role="status">Demo mode: these are synthetic test jobs, not real listings.</div>}
      {health && !health.llm.configured && <div className="banner info" role="status">Basic mode: no AI model is configured, so I use built-in rules. Search, matching and application tracking all still work.</div>}

      <main className="chat" aria-label="Conversation">
        <div className="log" role="log" aria-live="polite" aria-relevant="additions">
          {messages.map((m, idx) => (
            <div key={m.id} className={`msg ${m.role}`}>
              <div className="bubble">
                {m.error ? (
                  <div className="error" role="alert">
                    <strong>⚠ {m.error.message}</strong>
                    {m.error.recovery && <div>{m.error.recovery}</div>}
                    {m.error.retry && <button className="btn ghost" onClick={m.error.retry}>Retry</button>}
                  </div>
                ) : <p><Rich text={m.text} /></p>}
                {m.data?.profile && <ProfileCard key={JSON.stringify(m.data.profile)} profile={m.data.profile} disabled={busy}
                  onSave={saveProfile} onConfirm={() => void send("looks good", "Looks good — find jobs")} />}
                {m.data?.applications && m.data.applications.length > 0 && <ApplicationsTable rows={m.data.applications} />}
                {m.data?.jobs?.map((c) => <JobCard key={c.job.job_id} card={c} disabled={busy}
                  onAction={(p, d) => void send(p, d)} />)}
                {m.data?.pending_action && idx === messages.length - 1 && (
                  <div className="row confirm" role="group" aria-label="Confirm application">
                    <button className="btn primary" disabled={busy} onClick={() => void send("yes", "Yes, save this application")}>Yes, save application</button>
                    <button className="btn ghost" disabled={busy} onClick={() => void send("cancel", "Cancel")}>Cancel</button>
                  </div>
                )}
                {m.data && m.role === "assistant" && m.data.jobs && /show more/i.test(m.text) && idx === messages.length - 1 && (
                  <button className="btn ghost" disabled={busy} onClick={() => void send("show more")}>Show more</button>
                )}
                {m.role === "assistant" && m.data?.mode === "llm" && <span className="tag">AI-assisted</span>}
              </div>
            </div>
          ))}
          {busy && (
            <div className="msg assistant"><div className="bubble typing" role="status" aria-label="Working">
              {uploadPct !== null
                ? <><div className="bar" aria-hidden><span style={{ width: `${uploadPct}%` }} /></div>
                    <span>{parsing ? "Reading your resume…" : `Uploading… ${uploadPct}%`}</span></>
                : <span className="dots" aria-hidden><i /><i /><i /></span>}
            </div></div>
          )}
          <div ref={endRef} />
        </div>
      </main>

      {dragging && <div className="drop" aria-hidden>Drop your PDF or DOCX resume to upload</div>}

      <footer className="composer">
        {suggestions.length > 0 && (
          <div className="suggest" aria-label="Suggested prompts">
            {suggestions.map((s) => <button key={s} className="chip btnchip" disabled={busy} onClick={() => void send(s)}>{s}</button>)}
          </div>
        )}
        <form onSubmit={onSubmit}>
          <input ref={fileRef} type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            hidden aria-label="Upload resume" onChange={(e) => void upload(e.target.files?.[0])} />
          <button type="button" className="icon" aria-label="Attach resume (PDF or DOCX)" onClick={() => fileRef.current?.click()} disabled={busy}>📎</button>
          <textarea ref={inputRef} value={input} rows={1} maxLength={2000} placeholder="Ask about jobs, e.g. “Show me remote jobs”"
            aria-label="Message" onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSubmit(e); } }} />
          <button className="btn primary" disabled={busy || !input.trim()}>Send</button>
        </form>
      </footer>
    </div>
  );
}
