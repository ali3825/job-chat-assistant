import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { ChatResponse, JobCardData } from "./types";

const health = { status: "ok", dataset: { count: 10, is_test_fixture: false, sources: {} }, llm: { configured: false, provider: null, mode: "fallback" }, notice: "x" };
const base: ChatResponse = { reply: "", mode: "fallback", profile: null, jobs: null, applications: null, pending_action: null, suggestions: [], filters: null };
const job = (rank: number): JobCardData => ({
  rank, applied: false, application_status: null,
  job: { job_id: `j_abc${rank}`, title: `Python Dev ${rank}`, company: "Acme", location: "Remote", experience_required: "2+ years", skills: ["python"],
    description: "d", source_url: "https://example.test/1", employment_type: null, remote_status: "remote", posted_date: "2026-10-01",
    application_url: "https://example.test/apply/1", salary: null, source_name: "arbeitnow", scraped_at: null, last_verified_at: "2026-10-20T00:00:00Z", listing_status: "listed" },
  match: { score: 82, confidence: "high", matched_skills: ["python"], partial_skills: [], missing_skills: ["docker"], uncertainties: [], explanation: "Matches your skills in python.", components: [] },
});

function mockFetch(handler: (url: string, init?: RequestInit) => unknown) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const r = handler(url, init) as { status?: number; body: unknown };
    return { ok: (r.status ?? 200) < 400, status: r.status ?? 200, json: async () => r.body } as Response;
  }));
}

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("chat UI", () => {
  it("sends a message with a client id and renders job cards with honest status", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetch((url, init) => {
      calls.push({ url, init });
      if (url.endsWith("/health")) return { body: health };
      return { body: { ...base, reply: "Here's what I found", jobs: [job(1), job(2)] } };
    });
    render(<App />);
    await userEvent.type(screen.getByLabelText("Message"), "Show me remote jobs{enter}");
    expect(await screen.findByText("Python Dev 1", { exact: false })).toBeInTheDocument();
    const chat = calls.find((c) => c.url.endsWith("/api/chat"))!;
    expect((chat.init!.headers as Record<string, string>)["X-Client-Id"]).toMatch(/^c-/);
    expect(JSON.parse(chat.init!.body as string)).toEqual({ message: "Show me remote jobs" });
    const card = screen.getAllByRole("article")[0];
    expect(within(card).getByText("82")).toBeInTheDocument();
    expect(within(card).getByText(/✗ docker/)).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: /View original posting/ })).toHaveAttribute("href", "https://example.test/1");
  });

  it("apply button sends an id-based request, then shows confirm buttons", async () => {
    const bodies: string[] = [];
    mockFetch((url, init) => {
      if (url.endsWith("/health")) return { body: health };
      bodies.push(init!.body as string);
      if (bodies.length === 1) return { body: { ...base, reply: "Found", jobs: [job(3)] } };
      return { body: { ...base, reply: "Apply to job 3?", jobs: [job(3)], pending_action: { type: "confirm_apply", job_id: "j_abc3" } } };
    });
    render(<App />);
    await userEvent.type(screen.getByLabelText("Message"), "jobs{enter}");
    await userEvent.click(await screen.findByRole("button", { name: /Apply…/ }));
    expect(JSON.parse(bodies[1]).message).toBe("apply to j_abc3");
    expect(await screen.findByRole("button", { name: /Yes, save application/ })).toBeInTheDocument();
  });

  it("shows an error with a retry action when the server is unreachable", async () => {
    let fail = true;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/health")) return { ok: true, status: 200, json: async () => health } as Response;
      if (fail) throw new TypeError("network");
      return { ok: true, status: 200, json: async () => ({ ...base, reply: "back online" }) } as Response;
    }));
    render(<App />);
    await userEvent.type(screen.getByLabelText("Message"), "hello{enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("couldn't reach the server");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("back online")).toBeInTheDocument();
  });

  it("shows server-provided error message and recovery for a bad upload", async () => {
    mockFetch(() => ({ body: health }));
    class FakeXHR {
      upload = { onprogress: null as null | ((e: unknown) => void) };
      status = 415; responseText = JSON.stringify({ error: { code: "unsupported_type", message: "Only PDF and DOCX resumes are supported.", recovery: "Save your resume as a PDF or DOCX." } });
      onload: (() => void) | null = null; onerror: (() => void) | null = null;
      open() {} setRequestHeader() {}
      send() { setTimeout(() => this.onload?.(), 0); }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    render(<App />);
    const file = new File(["x"], "cv.txt", { type: "text/plain" });
    await userEvent.upload(screen.getByLabelText("Upload resume"), file, { applyAccept: false });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Only PDF and DOCX resumes are supported.");
    expect(alert).toHaveTextContent("Save your resume as a PDF or DOCX.");
  });

  it("renders the extracted profile with 'not specified' for missing fields and inferred markers", async () => {
    mockFetch((url) => url.endsWith("/health") ? { body: health } : { body: base });
    class FakeXHR {
      upload = {}; status = 200; onload: (() => void) | null = null; onerror = null;
      responseText = JSON.stringify({ ...base, reply: "I've read your resume.", profile: {
        name: "Priya Sharma", email: "p@example.com", phone: null, links: [], designation: "Backend Developer", skills: ["python"],
        years_experience: null, education: [], projects: [], certifications: [], preferred_locations: [], work_arrangement: null,
        provenance: { name: "inferred" }, confirmed: false } });
      open() {} setRequestHeader() {} send() { setTimeout(() => this.onload?.(), 0); }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    render(<App />);
    await userEvent.upload(screen.getByLabelText("Upload resume"), new File(["%PDF-"], "cv.pdf", { type: "application/pdf" }));
    const profile = await screen.findByLabelText("Extracted resume details");
    expect(within(profile).getByText("Priya Sharma")).toBeInTheDocument();
    expect(within(profile).getByText("inferred")).toBeInTheDocument();
    expect(within(profile).getAllByText(/not specified/).length).toBeGreaterThanOrEqual(3);
    expect(within(profile).getByRole("button", { name: /Looks good/ })).toBeInTheDocument();
  });

  it("shows banners for an empty dataset", async () => {
    mockFetch(() => ({ body: { ...health, dataset: { count: 0, is_test_fixture: false, sources: {} } } }));
    render(<App />);
    await waitFor(() => expect(screen.getByText(/dataset is empty/i)).toBeInTheDocument());
  });
});
