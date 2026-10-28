import type { JobCardData } from "../types";

const STATUS_TEXT: Record<string, string> = {
  external_application_required: "Saved — apply on the employer's page",
  applied_by_user: "Applied (confirmed by you)",
};

export function JobCard({ card, onAction, disabled }: {
  card: JobCardData; onAction: (payload: string, display: string) => void; disabled?: boolean;
}) {
  const { job, match } = card;
  const link = job.application_url || job.source_url;
  const tone = match ? (match.score >= 75 ? "high" : match.score >= 50 ? "mid" : "low") : "none";
  return (
    <article className="job" aria-label={`${job.title} at ${job.company}`}>
      <header className="job-head">
        <div>
          <h3><span className="rank">#{card.rank}</span> {job.title}</h3>
          <p className="muted">{job.company} · {job.location}</p>
        </div>
        {match && (
          <div className={`score ${tone}`} title="Heuristic fit score, not a hiring probability">
            <strong>{match.score}</strong><span>/100</span>
          </div>
        )}
      </header>
      <div className="chips">
        <span className={`chip badge-${job.remote_status}`}>{job.remote_status === "unknown" ? "work mode unknown" : job.remote_status}</span>
        <span className="chip">{job.experience_required}</span>
        {job.employment_type && <span className="chip">{job.employment_type.replace("_", " ")}</span>}
        {job.salary && <span className="chip">{job.salary}</span>}
      </div>
      {match && (
        <>
          <p className="why">{match.explanation}</p>
          {(match.matched_skills.length > 0 || match.partial_skills.length > 0) && (
            <div className="chips" aria-label="Matched skills">
              {match.matched_skills.map((s) => <span key={s} className="chip ok">✓ {s}</span>)}
              {match.partial_skills.map((s) => <span key={s} className="chip part">≈ {s}</span>)}
            </div>
          )}
          {match.missing_skills.length > 0 && (
            <div className="chips" aria-label="Missing skills">
              {match.missing_skills.map((s) => <span key={s} className="chip miss">✗ {s}</span>)}
            </div>
          )}
          {match.uncertainties.length > 0 && <p className="uncertain">ℹ {match.uncertainties.join(" ")}</p>}
        </>
      )}
      <p className="muted small">
        Listed on {job.source_name}{job.posted_date ? ` · posted ${job.posted_date}` : ""}
        {job.last_verified_at ? ` · seen ${job.last_verified_at.slice(0, 10)}` : ""} — may have closed since.
      </p>
      <footer className="job-actions">
        <a className="btn ghost" href={job.source_url} target="_blank" rel="noopener noreferrer">View original posting ↗</a>
        {match && (
          <button className="btn ghost" disabled={disabled}
            onClick={() => onAction(`explain why ${job.job_id} matches my profile`, `Why does job ${card.rank} match my profile?`)}>
            Why this match?
          </button>
        )}
        {card.applied ? (
          <span className="status-pill">{STATUS_TEXT[card.application_status ?? ""] ?? card.application_status}
            {card.application_status === "external_application_required" && (
              <> · <a href={link} target="_blank" rel="noopener noreferrer">Open application page ↗</a></>
            )}
          </span>
        ) : (
          <button className="btn primary" disabled={disabled}
            onClick={() => onAction(`apply to ${job.job_id}`, `Apply to job ${card.rank}: ${job.title}`)}>
            Apply…
          </button>
        )}
      </footer>
    </article>
  );
}
