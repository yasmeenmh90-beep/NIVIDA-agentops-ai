import type { IncidentDetail } from "../api/client";

type State = "done" | "active" | "waiting" | "error" | "todo";

const STAGES = [
  { key: "detector", label: "Detect", hint: "Parse logs, group repeats" },
  { key: "investigator", label: "Investigate", hint: "Root cause in code" },
  { key: "fixer", label: "Fix", hint: "Minimal patch" },
  { key: "tester", label: "Test", hint: "pytest in sandbox" },
  { key: "approval_gate", label: "Approve", hint: "Human decision" },
] as const;

function stageStates(d: IncidentDetail): State[] {
  const order = ["DETECTED", "INVESTIGATING", "FIXING", "TESTING", "AWAITING_APPROVAL"];
  const s = d.status;
  if (s === "PR_CREATED") return ["done", "done", "done", "done", "done"];
  if (s === "REJECTED") return ["done", "done", "done", "done", "error"];
  const idx = order.indexOf(s);
  if (idx >= 0) {
    return STAGES.map((_, i) => (i < idx ? "done" : i === idx ? (i === 4 ? "waiting" : i === 0 ? "done" : "active") : "todo"));
  }
  // FAILED / NEEDS_HUMAN: mark the last stage that ran as the error
  const ran = new Set(d.steps.map((x) => x.agent));
  const last = Math.max(...STAGES.map((st, i) => (ran.has(st.key) ? i : 0)));
  return STAGES.map((_, i) => (i < last ? "done" : i === last ? "error" : "todo"));
}

const DOT: Record<State, string> = {
  done: "bg-pass border-pass",
  active: "bg-run border-run pulse-dot",
  waiting: "bg-gate border-gate",
  error: "bg-fail border-fail",
  todo: "bg-panel border-line",
};

export function PipelineTrack({ d }: { d: IncidentDetail }) {
  const states = stageStates(d);
  const attempts = d.patches.length;
  return (
    <ol className="grid grid-cols-5 gap-0" aria-label="Pipeline progress">
      {STAGES.map((st, i) => (
        <li key={st.key} className="relative pr-2">
          {i < STAGES.length - 1 && (
            <span aria-hidden className={`absolute left-3 right-0 top-[9px] h-0.5 ${states[i] === "done" ? "bg-pass" : "bg-line"}`} />
          )}
          <span aria-hidden className={`relative z-10 block h-5 w-5 rounded-full border-2 ${DOT[states[i]]}`} />
          <div className="mt-2 font-bold leading-tight">{st.label}</div>
          <div className="text-xs text-muted leading-snug">
            {st.key === "fixer" && attempts > 0 ? `${attempts} attempt${attempts > 1 ? "s" : ""}` : st.hint}
          </div>
          <span className="sr-only">{states[i]}</span>
        </li>
      ))}
    </ol>
  );
}
