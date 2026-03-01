const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function fetchAPI<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API error ${res.status}: ${body}`);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Run {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  created_at: string;
  finished_at: string | null;
  total_jobs: number;
  completed_jobs: number;
  failed_jobs: number;
  skipped_jobs: number;
}

export interface Job {
  id: string;
  run_id: string;
  thread_id: string;
  subject: string;
  status: "queued" | "running" | "completed" | "failed" | "skipped";
  current_step: string;
  attempt: number;
  error_msg: string | null;
  intent: string | null;
  confidence: number | null;
  draft_id: string | null;
  summary: string | null;
  tokens_used: number;
  duration_ms: number;
}

export interface Step {
  id: string;
  job_id: string;
  name: string;
  status: "pending" | "running" | "completed" | "failed" | "skipped";
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number;
  error_msg: string | null;
}

export interface AuthStatus {
  authenticated: boolean;
  email: string | null;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export async function getAuthStatus(): Promise<AuthStatus> {
  return fetchAPI<AuthStatus>("/api/auth/status");
}

export async function getAuthLoginUrl(): Promise<{ auth_url: string }> {
  return fetchAPI<{ auth_url: string }>("/api/auth/login");
}

export async function logout(): Promise<void> {
  await fetchAPI("/api/auth/logout", { method: "POST" });
}

export async function createRun(maxThreads: number = 100): Promise<Run> {
  return fetchAPI<Run>("/api/runs", {
    method: "POST",
    body: JSON.stringify({ max_threads: maxThreads }),
  });
}

export async function listRuns(): Promise<Run[]> {
  return fetchAPI<Run[]>("/api/runs");
}

export async function getRun(runId: string): Promise<Run> {
  return fetchAPI<Run>(`/api/runs/${runId}`);
}

export async function listJobs(runId: string): Promise<Job[]> {
  return fetchAPI<Job[]>(`/api/runs/${runId}/jobs`);
}

export async function listSteps(runId: string, jobId: string): Promise<Step[]> {
  return fetchAPI<Step[]>(`/api/runs/${runId}/jobs/${jobId}/steps`);
}

export function getSSEUrl(runId: string): string {
  return `${API_BASE}/api/events/${runId}`;
}

export function getGlobalSSEUrl(): string {
  return `${API_BASE}/api/events/global/stream`;
}
