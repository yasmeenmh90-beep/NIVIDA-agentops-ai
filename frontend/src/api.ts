import { useEffect, useRef, useState } from "react";

// ---------- types (mirror backend/app/schemas.py) ----------

export type Status =
  | "DETECTED" | "INVESTIGATING" | "FIXING" | "TESTING" | "AWAITING_APPROVAL" | "PR_CREATED"
  | "REVISING" | "RESOLVED" | "PR_CLOSED" | "REJECTED" | "NEEDS_HUMAN" | "FAILED";

export type Severity = "low" | "medium" | "high" | "critical";

export interface Incident {
  id: number;
  project_id: number;
  commit_sha: string;
  title: string;
  summary: string;
  severity: Severity;
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
  pr_number: number | null;
  pr_url: string;
  pr_branch: string;
  revisions: number;
  regression_of: number | null;
  resolved_at: string | null;
  first_seen: string;
  last_seen: string;
  updated_at: string;
}

export interface RootCause {
  file?: string;
  line?: number;
  function?: string;
  root_cause?: string;
  evidence?: string[];
  fix_suggestion?: string;
  confidence?: string;
  tavily_sources?: { query?: string; title?: string; url: string }[];
  tool_rounds?: number;
}

export interface SimilarIncident {
  incident_id: number;
  score: number;
  status: string;
  title: string;
  root_cause_file?: string;
  root_cause_line?: number;
  root_cause?: string;
  fix_explanation?: string;
  diff?: string;
  pr_url?: string;
}

export interface TestSummary {
  verified?: boolean;
  problems?: string[];
  regression_tests?: string[];
  non_reproducing_tests?: string[];
  fixed?: string[];
  regressions?: string[];
  still_failing?: string[];
  before?: { passed: number; failed: number };
  after?: { passed: number; failed: number };
  runner?: string;
  duration_s?: number;
}

export interface Patch {
  id: number;
  attempt: number;
  kind: "initial" | "revision" | string;
  feedback: string;
  explanation: string;
  diff: string;
  tests_passed: boolean | null;
  regression_tests: string[];
  test_summary: TestSummary | null;
  test_log: string;
  created_at: string;
}

export interface ToolCallRec {
  tool: string;
  args: Record<string, unknown>;
  result_preview?: string;
  ts?: string;
}

export interface Step {
  id: number;
  agent: string;
  status: "running" | "done" | "error" | string;
  input_summary: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  output: Record<string, unknown> | null;
  tool_calls: ToolCallRec[];
  started_at: string;
  finished_at: string | null;
}

export interface Decision {
  id: number;
  decision: string;
  reason: string;
  decided_by: string;
  pr_url: string;
  decided_at: string;
}

export interface AuditEvent {
  id: number;
  actor: string;
  action: string;
  detail: Record<string, unknown>;
  ts: string;
}

export interface IncidentDetail extends Incident {
  traceback: string;
  running: boolean;
  root_cause: RootCause | null;
  similar_incidents: SimilarIncident[];
  steps: Step[];
  patches: Patch[];
  decisions: Decision[];
  audit: AuditEvent[];
}

export interface Project {
  id: number;
  name: string;
  repo_url: string;
  default_branch: string;
  app_path: string;
  github_repo: string;
  github_token_env: string;
  test_command: string;
  install_command: string;
  python_env: string;
  sandbox_mode: string;
  sandbox_image: string;
  protected_paths: string;
  setup_status: "pending" | "running" | "ready" | "failed" | string;
  head_sha: string;
  synced_at: string | null;
  created_at: string;
  setup_log?: string;
  github_token_present?: boolean;
  effective_test_command?: string;
  effective_sandbox?: string;
}

export interface TimingStat { count: number; median: number | null; mean: number | null; p90: number | null }

export interface Metrics {
  project: string | null;
  incidents: { total: number; by_status: Record<string, number>; by_severity: Record<string, number>; regressions: number; total_occurrences: number };
  fix_quality: {
    verified_fix_rate: number | null;
    first_attempt_success_rate: number | null;
    avg_attempts_to_verified: number | null;
    needs_human_now: number;
    ever_needed_human: number;
    approval_rate: number | null;
    merged: number;
    revisions: { total: number; verified: number };
  };
  timing_minutes: Record<"detect_to_verified_fix" | "detect_to_pr" | "detect_to_merged_mttr", TimingStat>;
  llm_tokens: { total: number; per_incident: TimingStat };
}

export interface ServerStatus {
  llm: { configured: boolean; provider: string; reasoning_model: string; triage_model: string };
  tavily: { configured: boolean };
  auth_enabled: boolean;
  webhooks: { github: boolean; sentry: boolean };
  notifications: { slack: boolean; webhook: boolean; email: boolean };
  require_regression_test: boolean;
  default_sandbox: string;
  max_fix_attempts: number;
  auto_investigate: boolean;
  max_revisions?: number;
  public_base_url?: string;
  notify_min_severity?: string;
}

export type Role = "viewer" | "member" | "admin";

export interface UserAccount {
  id: number;
  email: string;
  name: string;
  role: Role;
  github_login: string;
  avatar_url: string;
  has_password: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface Me { kind: "user" | "key" | "open"; name: string; role: Role; user: UserAccount | null }

export interface AuthConfig { password: boolean; github: boolean; signup: string; has_users: boolean; api_keys: boolean }

export interface ActivityEvent {
  id: number;
  incident_id: number;
  incident_title: string;
  incident_status: Status;
  project: string;
  actor: string;
  action: string;
  detail: Record<string, any>;
  ts: string;
}

// ---------- connection settings ----------

const LS = { session: "agentops.session", url: "agentops.apiUrl", key: "agentops.apiKey", approver: "agentops.approver", alerts: "agentops.alerts", sound: "agentops.sound" };

function read(k: string): string | null {
  try { return localStorage.getItem(k); } catch { return null; }
}
function write(k: string, v: string) {
  try { localStorage.setItem(k, v); } catch { /* storage unavailable */ }
}

export function getConfig() {
  return {
    url: (read(LS.url) ?? import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(/\/+$/, ""),
    key: read(LS.key) ?? import.meta.env.VITE_AGENTOPS_KEY ?? "",
    session: read(LS.session) ?? "",
  };
}

/** The credential sent to the backend: a signed-in session wins over a service API key. */
export function credential(): string {
  const c = getConfig();
  return c.session || c.key;
}

export function setSession(token: string) {
  try { if (token) localStorage.setItem(LS.session, token); else localStorage.removeItem(LS.session); } catch { /* ignore */ }
  window.dispatchEvent(new Event("agentops:config"));
}

export function saveBackendUrl(url: string) {
  write(LS.url, url.trim().replace(/\/+$/, ""));
  window.dispatchEvent(new Event("agentops:config"));
}
export function saveConfig(url: string, key: string) {
  write(LS.url, url.trim().replace(/\/+$/, ""));
  write(LS.key, key.trim());
  window.dispatchEvent(new Event("agentops:config"));
}
export const getApprover = () => read(LS.approver) ?? "";
export const getAlertPrefs = () => ({ desktop: read(LS.alerts) === "1", sound: read(LS.sound) !== "0" });
export const saveAlertPrefs = (p: { desktop: boolean; sound: boolean }) => { write(LS.alerts, p.desktop ? "1" : "0"); write(LS.sound, p.sound ? "1" : "0"); };
export const saveApprover = (v: string) => write(LS.approver, v.trim());

// ---------- HTTP ----------

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

function detailMessage(body: unknown, status: number): string {
  const d = (body as { detail?: unknown })?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x: { loc?: unknown[]; msg?: string }) => `${(x.loc ?? []).slice(1).join(".")}: ${x.msg}`).join("; ");
  if (status === 401) return "Your session has ended. Sign in again.";
  if (status === 403) return "View-only access. Ask an admin to make you a member.";
  return `Request failed with status ${status}`;
}

export async function api<T>(path: string, init: { method?: string; body?: unknown; form?: FormData } = {}): Promise<T> {
  const { url, session, key } = getConfig();
  const headers: Record<string, string> = {};
  if (session) headers.Authorization = `Bearer ${session}`;
  else if (key) headers["X-API-Key"] = key;
  let body: BodyInit | undefined;
  if (init.form) body = init.form;
  else if (init.body !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(init.body); }
  let res: Response;
  try {
    res = await fetch(url + path, { method: init.method ?? (body ? "POST" : "GET"), headers, body, signal: AbortSignal.timeout(20_000) });
  } catch (err) {
    const timedOut = err instanceof DOMException && (err.name === "TimeoutError" || err.name === "AbortError");
    throw new ApiError(0, timedOut ? "The backend took too long to respond." : `Can't reach the backend at ${url}. Is it running, and is this origin in CORS_ORIGINS?`);
  }
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try { data = JSON.parse(text); }
    catch { throw new ApiError(res.status || 0, res.ok ? "The backend returned a response this page could not read." : `Request failed with status ${res.status}`); }
  }
  if (res.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new Event("agentops:unauthorized"));
  if (!res.ok) throw new ApiError(res.status, detailMessage(data, res.status));
  return data as T;
}

/** Re-render when connection settings change. */
export function useConfigVersion() {
  const [v, setV] = useState(0);
  useEffect(() => {
    const h = () => setV((x) => x + 1);
    window.addEventListener("agentops:config", h);
    return () => window.removeEventListener("agentops:config", h);
  }, []);
  return v;
}

/** One-shot GET with reload(). */
export function useApi<T>(path: string | null) {
  const version = useConfigVersion();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    setLoading(true);
    api<T>(path)
      .then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e: Error) => { if (alive) setError(e.message); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [path, tick, version]);
  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}

/**
 * Live data over Server-Sent Events (`update` events carry the full payload).
 * Falls back to polling the matching REST path if the stream can't connect.
 */
export function useLive<T>(streamPath: string, restPath: string) {
  const version = useConfigVersion();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"connecting" | "live" | "polling">("connecting");
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    const { url } = getConfig();
    let alive = true;
    const sep = streamPath.includes("?") ? "&" : "?";
    const es = new EventSource(`${url}${streamPath}${sep}api_key=${encodeURIComponent(credential())}`);
    const poll = () =>
      api<T>(restPath).then((d) => { if (alive) { setData(d); setError(null); } }).catch((e: Error) => alive && setError(e.message));

    es.addEventListener("update", (e) => {
      if (!alive) return;
      setData(JSON.parse((e as MessageEvent).data));
      setError(null);
      setMode("live");
    });
    es.onerror = () => {
      if (!alive || pollRef.current !== null) return;
      es.close();
      setMode("polling");
      poll();
      pollRef.current = window.setInterval(poll, 3000);
    };
    return () => {
      alive = false;
      es.close();
      if (pollRef.current !== null) { clearInterval(pollRef.current); pollRef.current = null; }
      setMode("connecting");
    };
  }, [streamPath, restPath, version]);

  return { data, error, mode };
}

// ---------- shared helpers ----------

export const RUNNING: Status[] = ["DETECTED", "INVESTIGATING", "FIXING", "TESTING", "REVISING"];

export const STATUS_LABEL: Record<Status, string> = {
  DETECTED: "Detected",
  INVESTIGATING: "Investigating",
  FIXING: "Writing fix",
  TESTING: "Testing",
  AWAITING_APPROVAL: "Needs approval",
  PR_CREATED: "PR open",
  REVISING: "Revising PR",
  RESOLVED: "Merged",
  PR_CLOSED: "PR closed",
  REJECTED: "Rejected",
  NEEDS_HUMAN: "Needs a human",
  FAILED: "Failed",
};

export function ago(iso: string | null | undefined): string {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function duration(a: string, b: string | null): string {
  if (!b) return "";
  const s = Math.max(0, (new Date(b).getTime() - new Date(a).getTime()) / 1000);
  if (s < 0.05) return "";
  return s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}

export const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);
export const num = (v: number) => v.toLocaleString();
