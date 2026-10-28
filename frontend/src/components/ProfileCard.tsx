import { useState } from "react";
import type { Profile } from "../types";

const tag = (p: Profile, k: string) => (p.provenance[k] === "inferred" ? <em className="tag" title="Guessed from the resume layout/dates">inferred</em> :
  p.provenance[k] === "user" ? <em className="tag user">edited</em> : null);

export function ProfileCard({ profile, onSave, onConfirm, disabled }: {
  profile: Profile; onSave: (patch: Partial<Profile>) => Promise<void>; onConfirm: () => void; disabled?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    name: profile.name ?? "", designation: profile.designation ?? "",
    years: profile.years_experience?.toString() ?? "", skills: profile.skills.join(", "),
    locations: profile.preferred_locations.join(", "), arrangement: profile.work_arrangement ?? "",
  });
  const NS = "not specified";

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    const patch: Record<string, unknown> = {
      name: form.name || null, designation: form.designation || null,
      skills: form.skills.split(",").map((s) => s.trim()).filter(Boolean),
      preferred_locations: form.locations.split(",").map((s) => s.trim()).filter(Boolean),
      work_arrangement: form.arrangement || null,
    };
    if (form.years.trim() !== "" && !Number.isNaN(Number(form.years))) patch.years_experience = Number(form.years);
    try { await onSave(patch as Partial<Profile>); setEditing(false); } finally { setBusy(false); }
  }

  if (editing) {
    return (
      <form className="profile" onSubmit={save} aria-label="Edit profile">
        <label>Name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        <label>Designation<input value={form.designation} onChange={(e) => setForm({ ...form, designation: e.target.value })} /></label>
        <label>Years of experience<input inputMode="decimal" value={form.years} onChange={(e) => setForm({ ...form, years: e.target.value })} /></label>
        <label>Skills (comma separated)<textarea rows={3} value={form.skills} onChange={(e) => setForm({ ...form, skills: e.target.value })} /></label>
        <label>Preferred locations (comma separated)<input value={form.locations} onChange={(e) => setForm({ ...form, locations: e.target.value })} /></label>
        <label>Work arrangement
          <select value={form.arrangement} onChange={(e) => setForm({ ...form, arrangement: e.target.value })}>
            <option value="">No preference</option><option value="remote">Remote</option>
            <option value="hybrid">Hybrid</option><option value="onsite">On-site</option>
          </select>
        </label>
        <div className="row">
          <button className="btn primary" disabled={busy}>{busy ? "Saving…" : "Save changes"}</button>
          <button type="button" className="btn ghost" onClick={() => setEditing(false)}>Cancel</button>
        </div>
      </form>
    );
  }
  return (
    <section className="profile" aria-label="Extracted resume details">
      <dl>
        <dt>Name</dt><dd>{profile.name ?? NS} {tag(profile, "name")}</dd>
        <dt>Contact</dt><dd>{[profile.email, profile.phone, ...profile.links].filter(Boolean).join(" · ") || NS}</dd>
        <dt>Designation</dt><dd>{profile.designation ?? NS} {tag(profile, "designation")}</dd>
        <dt>Experience</dt><dd>{profile.years_experience != null ? `${profile.years_experience} years` : NS} {tag(profile, "years_experience")}</dd>
        <dt>Skills</dt>
        <dd>{profile.skills.length ? <span className="chips">{profile.skills.map((s) => <span className="chip" key={s}>{s}</span>)}</span> : NS}</dd>
        <dt>Education</dt>
        <dd>{profile.education.length ? profile.education.map((e, i) => <div key={i}>{e.degree}{e.year ? ` (${e.year})` : ""}</div>) : NS}</dd>
        <dt>Projects</dt>
        <dd>{profile.projects.length ? profile.projects.map((p, i) => <div key={i}><strong>{p.name}</strong>{p.description ? ` — ${p.description}` : ""}</div>) : NS}</dd>
        {profile.certifications.length > 0 && (<><dt>Certifications</dt><dd>{profile.certifications.join("; ")}</dd></>)}
        <dt>Preferences</dt>
        <dd>{[profile.work_arrangement, ...profile.preferred_locations].filter(Boolean).join(", ") || NS}</dd>
      </dl>
      <div className="row">
        {!profile.confirmed && <button className="btn primary" disabled={disabled} onClick={onConfirm}>Looks good — find jobs</button>}
        {profile.confirmed && <span className="status-pill">Profile confirmed ✓</span>}
        <button className="btn ghost" onClick={() => setEditing(true)}>Edit details</button>
      </div>
    </section>
  );
}
