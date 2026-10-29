import { ReactNode, useEffect, useId, useMemo, useRef, useState } from "react";
import { IncidentDetail, Patch, RUNNING, STATUS_LABEL, Severity, Status, TestSummary } from "../api";

export function BrandMark({ className = "brand-mark" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="7" fill="#12161c" stroke="#3d5230" />
      <path d="M6 21h6l3-10 4 14 3-8h4" stroke="#8fce4a" strokeWidth="2.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Dialog({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const root = ref.current;
    const focusable = () => [...(root?.querySelectorAll<HTMLElement>("button, [href], input, textarea, select") ?? [])];
    focusable()[0]?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeRef.current();
      if (e.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); prev?.focus(); };
  }, []);
  return (
    <div className="dialog-backdrop" onMouseDown={() => closeRef.current()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref} onMouseDown={(e) => e.stopPropagation()}>
        <h2 id={titleId}>{title}</h2>
        {children}
      </div>
    </div>
  );
}

const STATUS_TONE: Record<Status, string> = {
  DETECTED: "run", INVESTIGATING: "run", FIXING: "run", TESTING: "run", REVISING: "run",
  AWAITING_APPROVAL: "wait", PR_CREATED: "good", RESOLVED: "done",
  PR_CLOSED: "", REJECTED: "", NEEDS_HUMAN: "bad", FAILED: "bad",
};

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span className={`status ${STATUS_TONE[status] ?? ""}`}>
      <i />
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

export function Sev({ level }: { level: Severity }) {
  return (
    <span className={`sev ${level}`} title={`Severity: ${level}`}>
      <span className="sev-bars"><b /><b /><b /><b /></span>
      {level}
    </span>
  );
}

export function LiveDot({ mode }: { mode: "connecting" | "live" | "polling" }) {
  const text = mode === "live" ? "Live" : mode === "polling" ? "Refreshing every 3 s" : "Connecting";
  return <span className={`live-dot ${mode}`}><i />{text}</span>;
}

export function ErrorBox({ message }: { message: string | null }) {
  if (!message) return null;
  return <div className="alert error" role="alert">{message}</div>;
}

// ---------------- pipeline rail ----------------

const STAGES = ["Detect", "Investigate", "Fix", "Test", "Approve", "Pull request", "Merge"];
const AGENT_STAGE: Record<string, number> = { detector: 0, investigator: 1, fixer: 2, tester: 3, approval_gate: 4 };
const STATUS_STAGE: Partial<Record<Status, number>> = {
  DETECTED: 0, INVESTIGATING: 1, FIXING: 2, TESTING: 3, AWAITING_APPROVAL: 4,
  PR_CREATED: 5, REVISING: 5, PR_CLOSED: 5, RESOLVED: 6, REJECTED: 4, NEEDS_HUMAN: 3,
};

export function PipelineRail({ inc }: { inc: IncidentDetail }) {
  const { current, tone, notes } = useMemo(() => {
    let current = STATUS_STAGE[inc.status];
    if (current === undefined) {
      const failed = [...inc.steps].reverse().find((s) => s.status === "error") ?? inc.steps[inc.steps.length - 1];
      current = failed ? AGENT_STAGE[failed.agent] ?? 0 : 0;
    }
    const tone = RUNNING.includes(inc.status)
      ? "running"
      : ["AWAITING_APPROVAL", "PR_CREATED"].includes(inc.status)
        ? "waiting"
        : inc.status === "RESOLVED"
          ? "done"
          : "stopped";
    const initial = inc.patches.filter((p) => p.kind !== "revision");
    const verified = inc.patches.some((p) => p.tests_passed);
    const notes: string[] = [
      inc.occurrences > 1 ? `${inc.occurrences} times` : "",
      inc.root_cause?.file ? `${inc.root_cause.file.split("/").pop()}:${inc.root_cause.line}` : "",
      initial.length ? `attempt ${initial.length}` : "",
      inc.patches.length ? (verified ? "verified" : "not verified") : "",
      inc.decisions.length ? inc.decisions[inc.decisions.length - 1].decided_by : "",
      inc.pr_number ? `#${inc.pr_number}${inc.revisions ? `, ${inc.revisions} revision${inc.revisions > 1 ? "s" : ""}` : ""}` : "",
      inc.resolved_at ? "merged" : "",
    ];
    return { current, tone, notes };
  }, [inc]);

  return (
    <ol className="rail" aria-label="Pipeline progress">
      {STAGES.map((label, i) => {
        const state = i < current ? "passed" : i === current ? `current ${tone}` : "";
        const stopped = i === current && tone === "stopped";
        return (
          <li key={label} className={`rail-stop ${state} ${stopped ? "stopped" : ""}`} style={{ ["--i" as string]: i }} aria-current={i === current ? "step" : undefined}>
            <span className="rail-node">{i < current || (i === current && tone === "done") ? "✓" : stopped ? "!" : i + 1}</span>
            <span className="rail-label">{label}</span>
            {notes[i] && i <= current && <span className="rail-note">{notes[i]}</span>}
          </li>
        );
      })}
    </ol>
  );
}

// ---------------- diff ----------------

interface DiffFile { name: string; isNew: boolean; isTest: boolean; rows: { kind: string; a?: number; b?: number; text: string }[] }

function parseDiff(diff: string): DiffFile[] {
  const files: DiffFile[] = [];
  let cur: DiffFile | null = null;
  let a = 0, b = 0;
  const lines = diff.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (l.startsWith("--- ") && lines[i + 1]?.startsWith("+++ ")) {
      const from = l.slice(4).replace(/^a\//, "");
      const to = lines[i + 1].slice(4).replace(/^b\//, "");
      const name = to === "/dev/null" ? from : to;
      cur = { name, isNew: from === "/dev/null", isTest: /(^|\/)tests?\/|test_[^/]*\.py$/.test(name), rows: [] };
      files.push(cur);
      i++;
      continue;
    }
    if (!cur) continue;
    const m = l.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
    if (m) { a = +m[1]; b = +m[2]; cur.rows.push({ kind: "hunk", text: l }); continue; }
    if (l.startsWith("+")) cur.rows.push({ kind: "add", b: b++, text: l });
    else if (l.startsWith("-")) cur.rows.push({ kind: "del", a: a++, text: l });
    else if (l.startsWith("\\")) continue;
    else if (l !== "" || i < lines.length - 1) cur.rows.push({ kind: "ctx", a: a++, b: b++, text: l || " " });
  }
  return files;
}

export function DiffView({ diff }: { diff: string }) {
  const files = useMemo(() => parseDiff(diff), [diff]);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(diff);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };
  if (!files.length) return <pre className="log">{diff || "No diff"}</pre>;
  return (
    <div className="diff">
      <div className="diff-file">
        <span className="muted">Proposed changes. Nothing here is committed until a pull request exists.</span>
        <button type="button" className="btn sm ghost" onClick={copy}>{copied ? "Copied" : "Copy diff"}</button>
      </div>
      {files.map((f) => (
        <div key={f.name}>
          <div className="diff-file">
            <span>{f.name}</span>
            <span className="tag">{f.isTest ? (f.isNew ? "New regression test" : "Test") : "Fix"}</span>
          </div>
          <table>
            <tbody>
              {f.rows.map((r, i) => (
                <tr key={i} className={r.kind}>
                  <td className="n">{r.kind === "hunk" ? "" : r.a ?? ""}</td>
                  <td className="n">{r.kind === "hunk" ? "" : r.b ?? ""}</td>
                  <td>{r.text}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

// ---------------- test proof ----------------

export function TestProof({ s, patch }: { s: TestSummary; patch: Patch }) {
  const before = s.before ?? { passed: 0, failed: 0 };
  const after = s.after ?? { passed: 0, failed: 0 };
  const reg = s.regression_tests ?? patch.regression_tests ?? [];
  const fixedOther = (s.fixed ?? []).filter((t) => !reg.includes(t));
  return (
    <div className="stack">
      {s.verified ? (
        <div className="alert ok">
          Verified: the new regression test failed on the original code and passes with the fix. {s.regressions?.length ? "" : "No existing test broke."}
        </div>
      ) : (
        <div className="alert error">
          Not verified. {(s.problems ?? []).join(" ")}
        </div>
      )}

      <div className="proof">
        <div className="proof-side">
          <h3>Before the fix</h3>
          <div className="proof-nums">
            <span className="pass">{before.passed}<small>passed</small></span>
            <span className="fail">{before.failed}<small>failed</small></span>
          </div>
        </div>
        <div className="proof-arrow" aria-hidden="true">⟶</div>
        <div className="proof-side">
          <h3>After the fix</h3>
          <div className="proof-nums">
            <span className="pass">{after.passed}<small>passed</small></span>
            <span className="fail">{after.failed}<small>failed</small></span>
          </div>
        </div>
      </div>

      {reg.length > 0 && (
        <div>
          <h3 style={{ marginBottom: 8 }}>Regression tests written by the agent</h3>
          <ul className="testlist">
            {reg.map((t) => (
              <li key={t}><span className="flip"><span className="fail">fail</span> → <span className="pass">pass</span></span>{t}</li>
            ))}
          </ul>
        </div>
      )}
      {fixedOther.length > 0 && (
        <div>
          <h3 style={{ marginBottom: 8 }}>Existing tests this also fixed</h3>
          <ul className="testlist">
            {fixedOther.map((t) => <li key={t}><span className="flip pass">now passing</span>{t}</li>)}
          </ul>
        </div>
      )}
      {(s.regressions ?? []).length > 0 && (
        <div>
          <h3 style={{ marginBottom: 8 }} className="fail">Tests this patch broke</h3>
          <ul className="testlist">{s.regressions!.map((t) => <li key={t}>{t}</li>)}</ul>
        </div>
      )}
      {(s.still_failing ?? []).length > 0 && (
        <details className="fold">
          <summary>{s.still_failing!.length} unrelated test{s.still_failing!.length > 1 ? "s" : ""} failing before and after</summary>
          <ul className="testlist" style={{ marginTop: 10 }}>{s.still_failing!.map((t) => <li key={t} className="faint">{t}</li>)}</ul>
        </details>
      )}
      <p className="small faint">
        Ran in the {s.runner ?? "?"} sandbox{s.duration_s ? ` in ${s.duration_s.toFixed(1)} s` : ""}.
      </p>
      {patch.test_log && (
        <details className="fold">
          <summary>Full pytest output</summary>
          <pre className="log" style={{ marginTop: 10 }}>{patch.test_log}</pre>
        </details>
      )}
    </div>
  );
}
