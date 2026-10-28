import type { ApiErrorBody, ChatResponse, Health, Profile } from "./types";

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export class ApiError extends Error {
  constructor(message: string, public recovery?: string | null, public code?: string) { super(message); }
}

export function getClientId(): string {
  try {
    let id = localStorage.getItem("jobpilot_client_id");
    if (!id) { id = "c-" + crypto.randomUUID(); localStorage.setItem("jobpilot_client_id", id); }
    return id;
  } catch {
    return "c-" + Math.random().toString(36).slice(2).padEnd(18, "0");
  }
}

async function toError(res: Response): Promise<ApiError> {
  try {
    const body = (await res.json()) as ApiErrorBody;
    return new ApiError(body.error.message, body.error.recovery, body.error.code);
  } catch {
    return new ApiError(`The server returned an error (${res.status}).`, "Please try again.");
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, { ...init, headers: { "X-Client-Id": getClientId(), ...(init.headers ?? {}) } });
  } catch {
    throw new ApiError("I couldn't reach the server.", "Check your connection and press Retry.", "network");
  }
  if (!res.ok) throw await toError(res);
  return (await res.json()) as T;
}

export const api = {
  health: () => request<Health>("/health"),
  chat: (message: string) =>
    request<ChatResponse>("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message }),
    }),
  patchProfile: (patch: Partial<Profile>) =>
    request<ChatResponse>("/api/profile", {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
    }),
  /** Upload with real progress events (fetch cannot report upload progress). */
  uploadResume(file: File, onProgress: (pct: number) => void): Promise<ChatResponse> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", BASE + "/api/resume/parse");
      xhr.setRequestHeader("X-Client-Id", getClientId());
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(Math.round((100 * e.loaded) / e.total)); };
      xhr.onerror = () => reject(new ApiError("I couldn't reach the server.", "Check your connection and try again.", "network"));
      xhr.onload = () => {
        try {
          const body = JSON.parse(xhr.responseText);
          if (xhr.status >= 200 && xhr.status < 300) resolve(body as ChatResponse);
          else reject(new ApiError(body.error?.message ?? "Upload failed.", body.error?.recovery, body.error?.code));
        } catch { reject(new ApiError("Unexpected server response.", "Please try again.")); }
      };
      const form = new FormData();
      form.append("file", file);
      xhr.send(form);
    });
  },
};
