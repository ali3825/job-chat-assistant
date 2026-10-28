import type { ApplicationRow } from "../types";

const LABEL: Record<string, string> = {
  external_application_required: "Saved · apply on employer page",
  applied_by_user: "Applied (you confirmed)",
};

export function ApplicationsTable({ rows }: { rows: ApplicationRow[] }) {
  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">Your saved applications</caption>
        <thead><tr><th scope="col">Job</th><th scope="col">Job ID</th><th scope="col">Date</th><th scope="col">Status</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td><a href={r.source_url} target="_blank" rel="noopener noreferrer">{r.job_title}</a><div className="muted small">{r.company}</div></td>
              <td><code>{r.job_id}</code></td>
              <td>{new Date(r.applied_at).toLocaleDateString()}</td>
              <td>{LABEL[r.status] ?? r.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
