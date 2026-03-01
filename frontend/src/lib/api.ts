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

export type PipelineType = "gmail" | "slides" | "sheets" | "docs" | "forms" | "drive" | "slack" | "generic";

export interface Job {
  id: string;
  run_id: string;
  thread_id: string;
  subject: string;
  status: string;
  current_step: string;
  attempt: number;
  error_msg: string | null;
  intent: string | null;
  confidence: number | null;
  draft_id: string | null;
  draft_text: string | null;
  summary: string | null;
  tokens_used: number;
  duration_ms: number;
  // Pipeline fields
  pipeline_type: PipelineType;
  task_instruction: string;
  live_view_url: string;
  artifacts: string; // JSON-encoded list of artifact objects
}

export interface TaskInput {
  description: string;
  pipeline_type?: string;
  url?: string;
  params?: Record<string, unknown>;
}

export interface PipelineInfo {
  type: string;
  name: string;
  description: string;
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

export interface InboxThread {
  id: string;
  subject: string;
  sender: string;
  snippet: string;
  date: string;
  unread: boolean;
}

export interface ThreadContent {
  thread_id: string;
  subject: string;
  sender: string;
  messages: { from: string; date: string; body: string }[];
  message_count: number;
}

export interface Question {
  id: string;
  job_id: string;
  run_id: string;
  question: string;
  context: string;
  answer: string | null;
  status: string;
  created_at: string;
  answered_at: string | null;
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export async function getAuthStatus(): Promise<AuthStatus> {
  return fetchAPI<AuthStatus>("/api/auth/status");
}

export async function startBrowserLogin(): Promise<{ status: string; message?: string; auth_url?: string }> {
  return fetchAPI<{ status: string; message?: string; auth_url?: string }>("/api/auth/login", {
    method: "POST",
  });
}

export async function logout(): Promise<void> {
  await fetchAPI("/api/auth/logout", { method: "POST" });
}

// ---------------------------------------------------------------------------
// Inbox
// ---------------------------------------------------------------------------

export async function fetchInboxThreads(limit: number = 50): Promise<InboxThread[]> {
  const data = await fetchAPI<{ threads: InboxThread[] }>(`/api/inbox/threads?limit=${limit}`);
  return data.threads;
}

export async function fetchThreadContent(threadId: string): Promise<ThreadContent> {
  return fetchAPI<ThreadContent>(`/api/inbox/threads/${threadId}`);
}

export async function batchFetchThreads(
  threadIds: string[]
): Promise<Record<string, ThreadContent | null>> {
  const data = await fetchAPI<{ results: Record<string, ThreadContent | null> }>(
    "/api/inbox/threads/batch",
    {
      method: "POST",
      body: JSON.stringify({ thread_ids: threadIds }),
    }
  );
  return data.results;
}

// ---------------------------------------------------------------------------
// Runs
// ---------------------------------------------------------------------------

export async function createRun(
  threadIds: string[],
  threadSubjects: Record<string, string> = {},
  tasks: TaskInput[] = [],
): Promise<Run> {
  return fetchAPI<Run>("/api/runs", {
    method: "POST",
    body: JSON.stringify({
      thread_ids: threadIds,
      thread_subjects: threadSubjects,
      tasks,
    }),
  });
}

export async function createTaskRun(tasks: TaskInput[]): Promise<Run> {
  return fetchAPI<Run>("/api/runs", {
    method: "POST",
    body: JSON.stringify({ tasks }),
  });
}

export async function listPipelines(): Promise<PipelineInfo[]> {
  return fetchAPI<PipelineInfo[]>("/api/pipelines");
}

export async function listRuns(): Promise<Run[]> {
  return fetchAPI<Run[]>("/api/runs");
}

export async function getRun(runId: string): Promise<Run> {
  return fetchAPI<Run>(`/api/runs/${runId}`);
}

export async function cancelRun(runId: string): Promise<{ status: string; cancelled_jobs: number }> {
  return fetchAPI<{ status: string; cancelled_jobs: number }>(`/api/runs/${runId}/cancel`, {
    method: "POST",
  });
}

export async function setVisibleJobs(runId: string, jobIds: string[]): Promise<void> {
  await fetchAPI(`/api/runs/${runId}/visible`, {
    method: "POST",
    body: JSON.stringify({ job_ids: jobIds }),
  });
}

export async function listJobs(runId: string): Promise<Job[]> {
  return fetchAPI<Job[]>(`/api/runs/${runId}/jobs`);
}

export async function listSteps(runId: string, jobId: string): Promise<Step[]> {
  return fetchAPI<Step[]>(`/api/runs/${runId}/jobs/${jobId}/steps`);
}

// ---------------------------------------------------------------------------
// Questions
// ---------------------------------------------------------------------------

export async function listQuestions(runId: string): Promise<Question[]> {
  return fetchAPI<Question[]>(`/api/runs/${runId}/questions`);
}

export async function answerQuestion(
  runId: string,
  jobId: string,
  answer: string
): Promise<void> {
  await fetchAPI(`/api/runs/${runId}/jobs/${jobId}/answer`, {
    method: "POST",
    body: JSON.stringify({ answer }),
  });
}

// ---------------------------------------------------------------------------
// Approve / Discard
// ---------------------------------------------------------------------------

export async function approveJob(
  runId: string,
  jobId: string,
  action: "approve" | "discard" = "approve",
  draftText?: string
): Promise<void> {
  await fetchAPI(`/api/runs/${runId}/jobs/${jobId}/approve`, {
    method: "POST",
    body: JSON.stringify({ action, draft_text: draftText || "" }),
  });
}

// ---------------------------------------------------------------------------
// SSE
// ---------------------------------------------------------------------------

export function getSSEUrl(runId: string): string {
  return `${API_BASE}/api/events/${runId}`;
}

export function getGlobalSSEUrl(): string {
  return `${API_BASE}/api/events/global/stream`;
}

// ---------------------------------------------------------------------------
// Profile
// ---------------------------------------------------------------------------

export interface UserProfile {
  id: string;
  email: string;
  style_profile: Record<string, unknown>;
  preferences: Record<string, unknown>;
  has_profile: boolean;
  updated_at?: string;
}

export async function getProfile(): Promise<UserProfile> {
  return fetchAPI<UserProfile>("/api/profile");
}

export async function analyzeProfile(): Promise<{ status: string; style_profile: Record<string, unknown> }> {
  return fetchAPI("/api/profile/analyze", { method: "POST" });
}

export async function updatePreferences(prefs: Record<string, unknown>): Promise<void> {
  await fetchAPI("/api/profile/preferences", {
    method: "PUT",
    body: JSON.stringify(prefs),
  });
}

export async function getCalendarAvailability(
  start?: string,
  end?: string
): Promise<{ events: Array<{ summary: string; start: string; end: string; status: string }> }> {
  const params = new URLSearchParams();
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  return fetchAPI(`/api/profile/calendar/availability?${params}`);
}

// ---------------------------------------------------------------------------
// Screenshots
// ---------------------------------------------------------------------------

export function getScreenshotUrl(path: string): string {
  return `${API_BASE}${path}`;
}

// ---------------------------------------------------------------------------
// Task Demos (recorded browser workflows)
// ---------------------------------------------------------------------------

export interface TaskDemo {
  id: string;
  name: string;
  instruction_summary: string;
  created_at: string;
}

export async function startDemoRecording(): Promise<{
  session_id: string;
  live_view_url: string;
}> {
  return fetchAPI("/api/demo/start", { method: "POST" });
}

export async function stopDemoRecording(
  sessionId: string,
  name: string
): Promise<TaskDemo> {
  return fetchAPI<TaskDemo>("/api/demo/stop", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, name }),
  });
}

export async function listDemos(): Promise<TaskDemo[]> {
  return fetchAPI<TaskDemo[]>("/api/demo/list");
}

export async function getDemo(demoId: string): Promise<TaskDemo> {
  return fetchAPI<TaskDemo>(`/api/demo/${demoId}`);
}

export async function deleteDemo(demoId: string): Promise<{ deleted: boolean; id: string }> {
  return fetchAPI(`/api/demo/${demoId}`, { method: "DELETE" });
}
