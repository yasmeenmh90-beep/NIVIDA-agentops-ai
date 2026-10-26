import type { Incident, Status } from "../api/client";

export const STATUS_LABEL: Record<Status, string> = {
  DETECTED: "Detected",
  INVESTIGATING: "Investigating",
  FIXING: "Writing fix",
  TESTING: "Running tests",
  AWAITING_APPROVAL: "Needs your approval",
  PR_CREATED: "PR opened",
  REJECTED: "Rejected",
  NEEDS_HUMAN: "Needs a human fix",
  FAILED: "Pipeline error",
};

export function statusTone(s: Status): string {
  if (s === "AWAITING_APPROVAL") return "text-gate";
  if (s === "PR_CREATED") return "text-pass";
  if (s === "FAILED" || s === "NEEDS_HUMAN") return "text-fail";
  if (s === "REJECTED") return "text-muted";
  return "text-run";
}

export function isRunning(s: Status) {
  return s === "INVESTIGATING" || s === "FIXING" || s === "TESTING" || s === "DETECTED";
}

export function StatusText({ status }: { status: Status }) {
  return (
    <span className={`inline-flex items-center gap-1.5 font-bold ${statusTone(status)}`}>
      {isRunning(status) && <span className="pulse-dot inline-block h-2 w-2 rounded-full bg-run" aria-hidden />}
      {STATUS_LABEL[status]}
    </span>
  );
}

const SEV: Record<Incident["severity"], string> = {
  low: "bg-[#e4e9ef] text-muted",
  medium: "bg-[#e3ecf8] text-run",
  high: "bg-[#fbe6e3] text-fail",
  critical: "bg-fail text-white",
};

export function Severity({ level }: { level: Incident["severity"] }) {
  return <span className={`rounded px-1.5 py-0.5 text-xs font-bold ${SEV[level]}`}>{level}</span>;
}

export function ago(iso: string) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

export function duration(a: string, b: string | null) {
  if (!b) return "";
  const s = Math.max(0, (new Date(b).getTime() - new Date(a).getTime()) / 1000);
  if (s < 0.05) return "instant";
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

export function Section({ title, aside, children }: { title: string; aside?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-md border border-line bg-panel">
      <header className="flex items-baseline justify-between gap-3 border-b border-line px-4 py-2.5">
        <h3 className="text-[15px] font-bold">{title}</h3>
        {aside && <div className="text-sm text-muted">{aside}</div>}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}
