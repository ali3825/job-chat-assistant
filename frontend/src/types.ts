export type RemoteStatus = "remote" | "hybrid" | "onsite" | "unknown";

export interface Job {
  job_id: string; title: string; company: string; location: string;
  experience_required: string; skills: string[]; description: string; source_url: string;
  employment_type: string | null; remote_status: RemoteStatus; posted_date: string | null;
  application_url: string | null; salary: string | null; source_name: string;
  scraped_at: string | null; last_verified_at: string | null; listing_status: string;
}
export interface ComponentScore { name: string; weight: number; score: number | null; note: string }
export interface MatchResult {
  score: number; confidence: "high" | "medium" | "low"; matched_skills: string[]; partial_skills: string[];
  missing_skills: string[]; uncertainties: string[]; explanation: string; components: ComponentScore[];
}
export interface JobCardData { rank: number; job: Job; match: MatchResult | null; applied: boolean; application_status: string | null }
export interface Profile {
  name: string | null; email: string | null; phone: string | null; links: string[]; designation: string | null;
  skills: string[]; years_experience: number | null;
  education: { degree: string; details: string | null; year: number | null }[];
  projects: { name: string; description: string | null }[]; certifications: string[];
  preferred_locations: string[]; work_arrangement: "remote" | "hybrid" | "onsite" | null;
  provenance: Record<string, string>; confirmed: boolean;
}
export interface ApplicationRow {
  id: number; job_id: string; job_title: string; company: string; source_url: string;
  application_url: string | null; status: string; applied_at: string; updated_at: string;
}
export interface ChatResponse {
  reply: string; mode: "fallback" | "llm"; profile: Profile | null; jobs: JobCardData[] | null;
  applications: ApplicationRow[] | null; pending_action: { type: "confirm_apply"; job_id: string } | null;
  suggestions: string[]; filters: { description: string } | null;
}
export interface ApiErrorBody { error: { code: string; message: string; recovery?: string | null } }
export interface Health {
  status: string; dataset: { count: number; is_test_fixture: boolean; sources: Record<string, number> };
  llm: { configured: boolean; provider: string | null; mode: string }; notice: string | null;
}
export interface ChatMessage {
  id: number; role: "user" | "assistant"; text: string; data?: ChatResponse;
  error?: { message: string; recovery?: string | null; retry?: () => void }; resolved?: boolean;
}
