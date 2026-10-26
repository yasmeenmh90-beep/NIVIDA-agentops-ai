export const API = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") || "http://localhost:8000";

export type Status =
  | "DETECTED" | "INVESTIGATING" | "FIXING" | "TESTING" | "AWAITING_APPROVAL"
  | "PR_CREATED" | "REJECTED" | "NEEDS_HUMAN" | "FAILED";

export interface Incident {
  id: number;
  title: string;
  summary: string;
  severity: "low" | "medium" | "high" | "critical";
  status: Status;
  status_reason: string;
  error_signature: string;
  exception_type: string;
  message: string;
  endpoint: string;
  service: string;
  location: { file?: string; line?: number; function?: string };
  occurrences: number;
  tokens_used: number;
  first_seen: string;
  last_seen: string;
  updated_at: string;
}

export interface ToolCallRec { tool: string; args: Record<string, unknown>; result_preview: string; ts: string }

export interface Step {
  id: number;
  agent: "detector" | "investigator" | "fixer" | "tester" | "approval_gate";
  status: "running" | "done" | "error";
  input_summary: string;
  output: Record<string, any> | null;
  tool_calls: ToolCallRec[];
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  started_at: string;
  finished_at: string | null;
}

export interface TestSummary {
  verified: boolean;
  fixed: string[];
  regressions: string[];
  still_failing: string[];
  before: { passed: number; failed: number };
  after: { passed: number; failed: number };
  runner: string;
  duration_s: number;
}

export interface Patch {
  id: number;
  attempt: number;
  diff: string;
  explanation: string;
  tests_passed: boolean | null;
  test_summary: TestSummary | null;
  test_log: string;
}

export interface Decision { id: number; decision: "approve" | "reject"; reason: string; decided_by: string; pr_url: string; decided_at: string }

export interface RootCause {
  file: string;
  line: number;
  function?: string;
  root_cause: string;
  evidence: string[];
  fix_suggestion: string;
  confidence: "low" | "medium" | "high";
  web_sources_used?: string[];
  tavily_sources?: { query: string; title: string; url: string }[];
}

export interface IncidentDetail extends Incident {
  traceback: string;
  root_cause: RootCause | null;
  running: boolean;
  steps: Step[];
  patches: Patch[];
  decisions: Decision[];
}

export interface SystemStatus {
  llm: { configured: boolean; reasoning_model: string; triage_model: string };
  tavily: { configured: boolean };
  github: { configured: boolean; repo: string; base: string };
  sandbox: { mode: string };
  max_fix_attempts: number;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(API + path, { headers: { "Content-Type": "application/json" }, ...init });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = (body as any).detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x: any) => x.msg).join("; ") : `HTTP ${r.status}`);
  }
  return body as T;
}

export const api = {
  status: () => req<SystemStatus>("/status"),
  approve: (id: number, approver: string, note: string) =>
    req<{ pr_url: string }>(`/incidents/${id}/approve`, { method: "POST", body: JSON.stringify({ approver, note }) }),
  reject: (id: number, approver: string, reason: string) =>
    req(`/incidents/${id}/reject`, { method: "POST", body: JSON.stringify({ approver, reason }) }),
  rerun: (id: number) => req(`/incidents/${id}/run`, { method: "POST" }),
  trigger: (bug: number) => req<{ status_codes: number[] }>(`/demo/trigger/${bug}`, { method: "POST" }),
  reset: () => req("/demo/reset", { method: "POST" }),
};

/** Subscribe to a Server-Sent Events endpoint; returns an unsubscribe function. */
export function subscribe<T>(path: string, onData: (d: T) => void, onError?: () => void): () => void {
  const es = new EventSource(API + path);
  es.addEventListener("update", (e) => onData(JSON.parse((e as MessageEvent).data)));
  es.onerror = () => onError?.();
  return () => es.close();
}
