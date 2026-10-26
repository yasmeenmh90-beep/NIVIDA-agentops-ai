import { useState } from "react";
import { api, type IncidentDetail, type Patch, type Step } from "../api/client";
import { DiffView } from "./DiffView";
import { PipelineTrack } from "./PipelineTrack";
import { Section, Severity, StatusText, ago, duration } from "./bits";

const AGENT_NAME: Record<Step["agent"], string> = {
  detector: "Detector",
  investigator: "Investigator",
  fixer: "Fixer",
  tester: "Tester",
  approval_gate: "Approval gate",
};

function shortTest(id: string) {
  return id.split("::").pop() ?? id;
}

function RootCause({ d }: { d: IncidentDetail }) {
  const rc = d.root_cause;
  if (!rc) {
    return <p className="text-muted">The Investigator hasn't reported yet.</p>;
  }
  const sources = rc.tavily_sources ?? [];
  return (
    <div className="space-y-3">
      <p className="font-mono text-[13px]">
        {rc.file}:{rc.line}
        {rc.function && <span className="text-muted"> in {rc.function}()</span>}
      </p>
      <p className="max-w-[72ch]">{rc.root_cause}</p>
      {rc.evidence?.length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-[14px] text-muted">
          {rc.evidence.map((e, i) => <li key={i}>{e}</li>)}
        </ul>
      )}
      {sources.length > 0 && (
        <div className="text-[14px]">
          <p className="font-bold">Web evidence via Tavily</p>
          <ul className="mt-1 space-y-0.5">
            {sources.slice(0, 4).map((s, i) => (
              <li key={i}>
                <a className="text-run underline underline-offset-2" href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Tests({ p }: { p: Patch }) {
  const [open, setOpen] = useState(false);
  const t = p.test_summary;
  return (
    <div className="space-y-3">
      {t ? (
        <>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
            <span>
              Before <b className="text-fail">{t.before.failed} failing</b> / {t.before.passed} passing
            </span>
            <span>
              After <b className={t.after.failed < t.before.failed ? "text-pass" : "text-fail"}>{t.after.failed} failing</b> / {t.after.passed} passing
            </span>
          </div>
          {t.fixed.length > 0 && (
            <p>Now passing: {t.fixed.map((x) => <code key={x} className="mr-2 font-mono text-[13px] text-pass">{shortTest(x)}</code>)}</p>
          )}
          {t.regressions.length > 0 && (
            <p className="text-fail">New failures: {t.regressions.map(shortTest).join(", ")}</p>
          )}
          {t.still_failing.length > 0 && (
            <p className="text-sm text-muted">
              Unrelated tests still failing (other open bugs): {t.still_failing.map(shortTest).join(", ")}
            </p>
          )}
        </>
      ) : (
        <p className="text-muted">No test run for this attempt.</p>
      )}
      {p.test_log && (
        <div>
          <button className="text-sm text-run underline underline-offset-2" onClick={() => setOpen(!open)}>
            {open ? "Hide pytest output" : "Show pytest output"}
          </button>
          {open && (
            <pre className="mt-2 max-h-80 overflow-auto rounded border border-line bg-[#f6f8fa] p-3 font-mono text-[12px] leading-5">
              {p.test_log}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

function Timeline({ steps }: { steps: Step[] }) {
  return (
    <ol className="space-y-3">
      {steps.map((s) => (
        <li key={s.id} className="border-l-2 border-line pl-3">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-bold">{AGENT_NAME[s.agent]}</span>
            <span className={s.status === "error" ? "text-fail text-sm" : s.status === "running" ? "text-run text-sm" : "text-sm text-muted"}>
              {s.status === "running" ? (s.agent === "approval_gate" ? "waiting" : "working") : s.status === "error" ? "error" : duration(s.started_at, s.finished_at)}
            </span>
            {s.model && <span className="text-xs text-muted">{s.model}</span>}
            {s.prompt_tokens + s.completion_tokens > 0 && (
              <span className="text-xs text-muted">{(s.prompt_tokens + s.completion_tokens).toLocaleString()} tokens</span>
            )}
          </div>
          <p className="text-sm text-muted">{s.input_summary}</p>
          {s.output?.summary && <p className="text-sm">{s.output.summary}</p>}
          {s.output?.error && <p className="text-sm text-fail">{String(s.output.error)}</p>}
          {s.tool_calls.length > 0 && (
            <details className="mt-1 text-sm">
              <summary className="cursor-pointer text-run">{s.tool_calls.length} tool call{s.tool_calls.length > 1 ? "s" : ""}</summary>
              <ul className="mt-1 space-y-2">
                {s.tool_calls.map((c, i) => (
                  <li key={i}>
                    <code className="font-mono text-[12px]">
                      {c.tool}({Object.entries(c.args).filter(([k]) => k !== "edits").map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})
                    </code>
                    <pre className="mt-0.5 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-[#f6f8fa] p-2 font-mono text-[11px] leading-4 text-muted">
                      {c.result_preview}
                    </pre>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </li>
      ))}
    </ol>
  );
}

function DecisionBar({ d }: { d: IncidentDetail }) {
  const [name, setName] = useState(() => localStorage.getItem("agentops.approver") ?? "");
  const [note, setNote] = useState("");
  const [mode, setMode] = useState<"idle" | "rejecting">("idle");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function act(kind: "approve" | "reject") {
    if (!name.trim()) return setErr("Enter your name so the decision is recorded in the audit log.");
    if (kind === "reject" && !note.trim()) return setErr("Add a reason for rejecting.");
    setBusy(true);
    setErr("");
    try {
      localStorage.setItem("agentops.approver", name.trim());
      if (kind === "approve") await api.approve(d.id, name.trim(), note.trim());
      else await api.reject(d.id, name.trim(), note.trim());
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="bottom-0 z-20 border-t-4 md:sticky border-gate bg-gate-soft px-5 py-4 shadow-[0_-6px_16px_rgba(28,36,48,0.08)]">
      <div className="flex flex-wrap items-end gap-3">
        <div className="mr-auto max-w-[52ch]">
          <p className="text-[17px] font-bold text-gate">Tests pass. Open a pull request?</p>
          <p className="text-sm text-muted">
            Approving creates a branch and a PR with the diagnosis, diff and test results. Nothing is merged.
          </p>
        </div>
        <label className="text-sm">
          <span className="block text-muted">Your name</span>
          <input value={name} onChange={(e) => setName(e.target.value)}
            className="w-40 rounded border border-line bg-panel px-2 py-1.5" />
        </label>
        <label className="text-sm">
          <span className="block text-muted">{mode === "rejecting" ? "Reason (required)" : "Note (optional)"}</span>
          <input value={note} onChange={(e) => setNote(e.target.value)}
            className="w-64 rounded border border-line bg-panel px-2 py-1.5" />
        </label>
        {mode === "idle" ? (
          <>
            <button disabled={busy} onClick={() => act("approve")}
              className="rounded bg-gate px-4 py-2 font-bold text-white hover:brightness-95 disabled:opacity-60">
              {busy ? "Opening PR…" : "Approve and open PR"}
            </button>
            <button disabled={busy} onClick={() => setMode("rejecting")}
              className="rounded border border-line bg-panel px-4 py-2 font-bold hover:bg-ground">Reject</button>
          </>
        ) : (
          <>
            <button disabled={busy} onClick={() => act("reject")}
              className="rounded bg-fail px-4 py-2 font-bold text-white disabled:opacity-60">Reject incident</button>
            <button onClick={() => setMode("idle")} className="px-2 py-2 text-sm text-run underline">Cancel</button>
          </>
        )}
      </div>
      {err && <p className="mt-2 text-sm text-fail" role="alert">{err}</p>}
    </div>
  );
}

export function IncidentView({ d }: { d: IncidentDetail }) {
  const verified = [...d.patches].reverse().find((p) => p.tests_passed) ?? null;
  const latest = verified ?? d.patches[d.patches.length - 1] ?? null;
  const decision = d.decisions[d.decisions.length - 1];
  const [busy, setBusy] = useState(false);

  return (
    <div className="flex min-h-full flex-col">
      <div className="flex-1 space-y-5 p-5 lg:p-7">
        <header className="space-y-2">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
            <StatusText status={d.status} />
            <Severity level={d.severity} />
            <span className="text-muted">#{d.id}</span>
            <span className="text-muted">{d.occurrences}× since {ago(d.first_seen)}</span>
            {d.endpoint && <code className="font-mono text-[12px] text-muted">{d.endpoint}</code>}
          </div>
          <h2 className="max-w-[60ch] text-2xl font-bold leading-tight">{d.title}</h2>
          {d.summary && <p className="max-w-[72ch] text-muted">{d.summary}</p>}
          {d.status_reason && <p className="text-sm text-muted">{d.status_reason}</p>}
        </header>

        <div className="rounded-md border border-line bg-panel px-4 pt-4 pb-3">
          <PipelineTrack d={d} />
        </div>

        {decision?.pr_url && (
          <p className="rounded-md border border-pass bg-[#e8f4ec] px-4 py-3">
            Approved by <b>{decision.decided_by}</b>.{" "}
            <a className="font-bold text-pass underline underline-offset-2" href={decision.pr_url} target="_blank" rel="noreferrer">View pull request</a>
          </p>
        )}
        {decision?.decision === "reject" && (
          <p className="rounded-md border border-line bg-panel px-4 py-3">
            Rejected by <b>{decision.decided_by}</b>: {decision.reason}
          </p>
        )}
        {(d.status === "FAILED" || d.status === "NEEDS_HUMAN") && (
          <div className="flex flex-wrap items-center gap-3 rounded-md border border-fail bg-[#fbecea] px-4 py-3">
            <p className="mr-auto">
              {d.status === "NEEDS_HUMAN"
                ? "No patch passed the tests, so nothing is proposed. Review the attempts below."
                : "The pipeline stopped with an error. Check the timeline, fix the cause, then run it again."}
            </p>
            <button disabled={busy} onClick={async () => { setBusy(true); try { await api.rerun(d.id); } finally { setBusy(false); } }}
              className="rounded border border-fail bg-panel px-3 py-1.5 font-bold text-fail">Run pipeline again</button>
          </div>
        )}

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
          <div className="min-w-0 space-y-5">
            <Section title="Root cause" aside={d.root_cause ? `${d.root_cause.confidence} confidence` : undefined}>
              <RootCause d={d} />
            </Section>

            <Section title="Proposed fix" aside={latest ? `attempt ${latest.attempt}` : undefined}>
              {latest ? (
                <div className="space-y-3">
                  {latest.explanation && <p className="max-w-[72ch]">{latest.explanation}</p>}
                  <DiffView diff={latest.diff} />
                </div>
              ) : (
                <p className="text-muted">No patch yet.</p>
              )}
            </Section>

            <Section title="Verification" aside={latest?.test_summary ? `${latest.test_summary.runner} · ${latest.test_summary.duration_s}s` : undefined}>
              {latest ? <Tests p={latest} /> : <p className="text-muted">Tests run after the first patch.</p>}
              {d.patches.length > 1 && (
                <p className="mt-3 text-sm text-muted">
                  Attempts: {d.patches.map((p) => `#${p.attempt} ${p.tests_passed ? "passed" : "failed"}`).join(", ")}
                </p>
              )}
            </Section>

            <details className="rounded-md border border-line bg-panel">
              <summary className="cursor-pointer px-4 py-2.5 font-bold">Original traceback</summary>
              <pre className="max-h-96 overflow-auto border-t border-line p-4 font-mono text-[12px] leading-5">{d.traceback}</pre>
            </details>
          </div>

          <Section title="Agent timeline" aside={d.tokens_used ? `${d.tokens_used.toLocaleString()} tokens` : undefined}>
            <Timeline steps={d.steps} />
          </Section>
        </div>
      </div>

      {d.status === "AWAITING_APPROVAL" && <DecisionBar key={d.id} d={d} />}
    </div>
  );
}
