import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  IncidentDetail, Patch, RUNNING, ServerStatus, Step, api, ago, duration, fmtTime, getApprover, num, saveApprover, useApi, useLive,
} from "../api";
import { Dialog, DiffView, ErrorBox, LiveDot, PipelineRail, Sev, StatusBadge, TestProof } from "../components/ui";
import { TermLine, Terminal, clock } from "../components/anim";
import { useMe } from "../auth";
import { toast } from "../toast";

// ---------------- decision panel ----------------

function DecisionPanel({ inc }: { inc: IncidentDetail }) {
  const { me, canAct } = useMe();
  const signedIn = me.kind === "user";
  const [approver, setApprover] = useState(signedIn ? me.name : getApprover());
  const [note, setNote] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<"" | "approve" | "reject" | "run">("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const latest = [...inc.patches].reverse()[0];
  const testsFailed = latest?.tests_passed === false || latest?.test_summary?.verified === false;

  const act = async (kind: "approve" | "reject" | "run") => {
    if (busy) return;
    setBusy(kind); setError(null);
    try {
      if (kind === "run") {
        await api(`/incidents/${inc.id}/run`, { method: "POST" });
        setDone("The pipeline was restarted.");
        toast("The pipeline was restarted.", "ok");
      } else {
        saveApprover(approver);
        const body = kind === "approve" ? { approver, note } : { approver, reason };
        const r = await api<{ pr_url?: string }>(`/incidents/${inc.id}/${kind}`, { body });
        if (kind === "approve") {
          const text = r.pr_url ? "The pull request was created." : "Approval was recorded. A pull request URL was not returned.";
          setDone(text);
          toast(text, "ok");
        } else {
          setDone("The fix was rejected.");
          toast("The fix was rejected.", "info");
        }
      }
    } catch (e) {
      setError((e as Error).message);
      toast((e as Error).message, "error");
    } finally {
      setBusy("");
      setConfirming(false);
    }
  };

  const lastDecision = inc.decisions[inc.decisions.length - 1];

  if (inc.status === "AWAITING_APPROVAL" && !canAct) {
    return (
      <section className="panel panel-pad stack decision" aria-label="Waiting for approval">
        <h2>Waiting for approval</h2>
        <p className="small muted">A verified fix is ready. A member or admin needs to approve it before a pull request is opened. You have view-only access.</p>
      </section>
    );
  }

  if (inc.status === "AWAITING_APPROVAL") {
    return (
      <section className="panel panel-pad decision stack" id="decision" aria-label="Your decision">
        <div>
          <h2>Your decision</h2>
          <p className="small muted" style={{ marginTop: 4 }}>
            Approving opens a pull request on a new branch. AgentOps does not merge it.
          </p>
        </div>
        {signedIn ? (
          <p className="small">Approving as <b>{me.name}</b></p>
        ) : (
          <div className="field">
            <label htmlFor="approver">Your name</label>
            <input id="approver" className="input" value={approver} onChange={(e) => setApprover(e.target.value)} placeholder="e.g. yasmeen" autoComplete="name" />
          </div>
        )}
        {!rejecting ? (
          <>
            <div className="field">
              <label htmlFor="note">Note for the PR (optional)</label>
              <input id="note" className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Looks good" />
            </div>
            <div className="row">
              <button className="btn primary grow" disabled={!approver.trim() || !!busy || testsFailed} onClick={() => setConfirming(true)}>
                {busy === "approve" ? <><span className="spinner" /> Opening PR</> : "Approve and create PR"}
              </button>
              <button className="btn danger" disabled={!!busy} onClick={() => setRejecting(true)}>Reject</button>
            </div>
          </>
        ) : (
          <>
            <div className="field">
              <label htmlFor="reason">Why are you rejecting it?</label>
              <textarea id="reason" className="textarea" style={{ minHeight: 90, fontFamily: "var(--sans)" }} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="The fix belongs in the validator" />
            </div>
            <div className="row">
              <button className="btn danger grow" disabled={!approver.trim() || !reason.trim() || !!busy} onClick={() => act("reject")}>
                {busy === "reject" ? <><span className="spinner" /> Rejecting</> : "Reject fix"}
              </button>
              <button className="btn ghost" onClick={() => setRejecting(false)}>Cancel</button>
            </div>
          </>
        )}
        {testsFailed && <div className="alert error">This patch did not pass verification, so approval stays closed.</div>}
        <ErrorBox message={error} />
        {done && <div className="alert ok">{done}{inc.pr_url ? <> <a href={inc.pr_url}>Open the pull request</a></> : null}</div>}
        {confirming && (
          <Dialog title="Create the pull request?" onClose={() => { if (!busy) setConfirming(false); }}>
            <p>{latest?.test_summary?.verified ? "The latest patch is marked verified. Review the diff once more, then confirm." : "The incident is waiting for approval. Confirm only if the diagnosis, diff, and test results are enough."}</p>
            <p>This creates a pull request. It does not merge or deploy.</p>
            <div className="row">
              <button type="button" className="btn ghost" disabled={!!busy} onClick={() => setConfirming(false)}>Cancel</button>
              <button type="button" className="btn primary" disabled={!!busy || testsFailed} onClick={() => act("approve")}>
                {busy === "approve" ? "Creating PR" : "Approve and create PR"}
              </button>
            </div>
            <ErrorBox message={error} />
          </Dialog>
        )}
      </section>
    );
  }

  if (inc.pr_url) {
    return (
      <section className="panel panel-pad decision-done stack" aria-label="Pull request">
        <h2>{inc.status === "RESOLVED" ? "Fix merged" : inc.status === "PR_CLOSED" ? "Pull request closed" : "Pull request open"}</h2>
        <a className="btn primary" href={inc.pr_url} target="_blank" rel="noreferrer">View PR #{inc.pr_number} on GitHub</a>
        <dl className="kv">
          {lastDecision && <><dt>Approved by</dt><dd>{lastDecision.decided_by}{lastDecision.reason ? `: “${lastDecision.reason}”` : ""}</dd></>}
          {inc.pr_branch && <><dt>Branch</dt><dd className="mono small">{inc.pr_branch}</dd></>}
          <dt>Revisions</dt><dd>{inc.revisions}</dd>
          {inc.resolved_at && <><dt>Merged</dt><dd>{fmtTime(inc.resolved_at)}</dd></>}
        </dl>
        {inc.status === "PR_CREATED" && canAct && <RevisionRequest inc={inc} />}
        {inc.status === "REVISING" && (
          <>
            <p className="small muted row"><span className="spinner" style={{ color: "var(--sky)" }} /> The Fixer is revising the PR and re-running the tests.</p>
            {canAct && <StopButton inc={inc} />}
          </>
        )}
      </section>
    );
  }

  if (inc.status === "REJECTED") {
    return (
      <section className="panel panel-pad stack">
        <h2>Rejected</h2>
        {lastDecision && <p className="muted">{lastDecision.decided_by}: “{lastDecision.reason}”</p>}
      </section>
    );
  }

  if (inc.status === "FAILED" || inc.status === "NEEDS_HUMAN" || (inc.status === "DETECTED" && !inc.running)) {
    return (
      <section className="panel panel-pad stack" style={{ borderColor: "rgba(242,112,109,.45)" }}>
        <h2>{inc.status === "NEEDS_HUMAN" ? "No verified fix" : inc.status === "FAILED" ? "Pipeline stopped" : "Not started"}</h2>
        <p className="small muted">
          {inc.status === "NEEDS_HUMAN"
            ? "None of the attempts passed verification, so nothing was proposed. Review the attempts below, or run the agents again."
            : inc.status_reason || "The agents haven't started on this incident."}
        </p>
        {canAct && (
          <button className="btn amber" disabled={!!busy} onClick={() => act("run")}>
            {busy === "run" ? <><span className="spinner" /> Starting</> : "Run the agents again"}
          </button>
        )}
        <ErrorBox message={error} />
        {done && <div className="alert ok">{done}</div>}
      </section>
    );
  }

  return (
    <section className="panel panel-pad stack">
      <h2 className="row"><span className="spinner" style={{ color: "var(--sky)" }} /> Agents are working</h2>
      <p className="small muted">{inc.status_reason || "This page updates live."}</p>
      {inc.running && canAct && <StopButton inc={inc} />}
    </section>
  );
}

function StopButton({ inc }: { inc: IncidentDetail }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const stop = async () => {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      await api(`/incidents/${inc.id}/cancel`, { body: { by: getApprover() || "dashboard" } });
      setMsg("Stopping at the next checkpoint, before the next tool call.");
      toast("The agents will stop at the next checkpoint.", "info");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); setConfirming(false); }
  };
  return (
    <div className="stack" style={{ gap: 8 }}>
      <button type="button" className="btn danger" onClick={() => setConfirming(true)} disabled={busy || !!msg}>{msg ? "Stopping" : "Stop the agents"}</button>
      {confirming && (
        <Dialog title="Stop the agents?" onClose={() => { if (!busy) setConfirming(false); }}>
          <p>The run stops at the next checkpoint. Work already finished stays in the incident.</p>
          <div className="row">
            <button type="button" className="btn ghost" disabled={busy} onClick={() => setConfirming(false)}>Keep running</button>
            <button type="button" className="btn danger" disabled={busy} onClick={stop}>{busy ? "Stopping" : "Stop the agents"}</button>
          </div>
        </Dialog>
      )}
      {msg && <p className="small faint">{msg}</p>}
      <ErrorBox message={error} />
    </div>
  );
}

function RevisionRequest({ inc }: { inc: IncidentDetail }) {
  const { me } = useMe();
  const signedIn = me.kind === "user";
  const { data: status } = useApi<ServerStatus>("/status");
  const max = status?.max_revisions ?? 3;
  const [author, setAuthor] = useState(signedIn ? me.name : getApprover());
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const left = Math.max(0, max - inc.revisions);
  const send = async () => {
    setBusy(true); setError(null);
    try {
      saveApprover(author);
      await api(`/incidents/${inc.id}/revise`, { body: { author, feedback } });
      setFeedback("");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  return (
    <div className="stack revise">
      <div>
        <h3>Ask for changes</h3>
        <p className="small muted" style={{ marginTop: 4 }}>
          The agent updates the PR, re-runs the tests, and pushes only if the fix still passes. Same as an <code>/agentops</code> comment on GitHub. {left} of {max} revisions left.
        </p>
      </div>
      <textarea className="textarea" style={{ minHeight: 80, fontFamily: "var(--sans)" }} value={feedback} onChange={(e) => setFeedback(e.target.value)} placeholder="e.g. Use a guard clause instead of the conditional" aria-label="What should change?" disabled={!left} />
      <div className="row">
        {!signedIn && <input className="input grow" style={{ width: "auto" }} value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="Your name" aria-label="Your name" />}
        <button className="btn amber" onClick={send} disabled={busy || !left || !feedback.trim() || !author.trim()}>{busy ? "Sending" : "Revise PR"}</button>
      </div>
      <ErrorBox message={error} />
    </div>
  );
}

// ---------------- PR history, regression, token usage ----------------

function PrHistory({ inc }: { inc: IncidentDetail }) {
  const approved = inc.decisions.find((d) => d.decision === "approve");
  const revisions = inc.patches.filter((p) => p.kind === "revision");
  if (!approved && !revisions.length) return null;
  const byRevision = new Map<number, Patch[]>();
  revisions.forEach((p) => { const n = Math.floor(p.attempt / 100); byRevision.set(n, [...(byRevision.get(n) ?? []), p]); });
  return (
    <section className="panel panel-pad stack">
      <h2>Pull request history</h2>
      <ol className="history">
        {approved && (
          <li className="h-ok">
            <span className="h-when">{fmtTime(approved.decided_at)}</span>
            <span><b>{approved.decided_by}</b> approved the fix and opened {inc.pr_number ? `PR #${inc.pr_number}` : "the PR"}{approved.reason ? `: “${approved.reason}”` : ""}</span>
          </li>
        )}
        {[...byRevision.entries()].map(([n, ps]) => {
          const ok = ps.some((p) => p.tests_passed);
          return (
            <li key={n} className={ok ? "h-ok" : "h-bad"}>
              <span className="h-when">{fmtTime(ps[0].created_at)}</span>
              <span>
                Revision {n}: <span className="muted">“{ps[0].feedback}”</span>{" "}
                {ok ? <span className="pass">verified and pushed</span> : <span className="fail">failed verification, nothing pushed</span>}
                {ps.length > 1 && <span className="faint"> ({ps.length} attempts)</span>}
              </span>
            </li>
          );
        })}
        {inc.status === "RESOLVED" && <li className="h-ok"><span className="h-when">{fmtTime(inc.resolved_at)}</span><span>Merged{inc.status_reason ? `: ${inc.status_reason}` : ""}</span></li>}
        {inc.status === "PR_CLOSED" && <li className="h-bad"><span className="h-when" /><span>{inc.status_reason}</span></li>}
      </ol>
    </section>
  );
}

function RegressionCard({ inc }: { inc: IncidentDetail }) {
  const fromMemory = inc.similar_incidents?.find((x) => x.incident_id === inc.regression_of);
  const { data: prev } = useApi<IncidentDetail>(inc.regression_of && !fromMemory?.diff ? `/incidents/${inc.regression_of}` : null);
  if (!inc.regression_of) return null;
  const prevPatch = prev ? [...prev.patches].reverse().find((p) => p.tests_passed) : null;
  const diff = fromMemory?.diff ?? prevPatch?.diff ?? "";
  const cause = fromMemory?.root_cause ?? prev?.root_cause?.root_cause ?? "";
  const pr = fromMemory?.pr_url ?? prev?.pr_url;
  return (
    <section className="panel regression" aria-label="This error came back">
      <div className="panel-pad stack" style={{ paddingBottom: 12 }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2>This error came back</h2>
          <Link className="chip warn" to={`/incidents/${inc.regression_of}`}>Fixed before in #{inc.regression_of}</Link>
        </div>
        <p className="small muted">
          The same error signature was fixed and merged earlier, so severity was raised and the earlier fix was given to the Investigator as memory.
          {pr && <> Earlier PR: <a href={pr} target="_blank" rel="noreferrer">{pr.replace("https://github.com/", "")}</a>.</>}
        </p>
        <div className="compare">
          <div><h3>Then</h3><p className="small">{cause || "—"}</p></div>
          <div><h3>Now</h3><p className="small">{inc.root_cause?.root_cause ?? "Investigating…"}</p></div>
        </div>
      </div>
      {diff && (
        <details className="fold" style={{ padding: "0 20px 16px" }}>
          <summary>The earlier fix</summary>
          <div style={{ marginTop: 10, border: "1px solid var(--line-soft)", borderRadius: 8, overflow: "hidden" }}><DiffView diff={diff} /></div>
        </details>
      )}
    </section>
  );
}

function TokenUsage({ inc }: { inc: IncidentDetail }) {
  const rows = useMemo(() => {
    const m = new Map<string, { p: number; c: number; model: string }>();
    inc.steps.forEach((s) => {
      if (!s.prompt_tokens && !s.completion_tokens) return;
      const key = s.agent === "detector" ? "triage" : s.agent;
      const r = m.get(key) ?? { p: 0, c: 0, model: s.model };
      r.p += s.prompt_tokens; r.c += s.completion_tokens;
      m.set(key, r);
    });
    return [...m.entries()];
  }, [inc.steps]);
  if (!rows.length) return null;
  const max = Math.max(...rows.map(([, r]) => r.p + r.c));
  return (
    <section className="panel panel-pad stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>Nemotron usage</h2>
        <span className="mono small muted">{num(inc.tokens_used)} tokens</span>
      </div>
      <div className="usage">
        {rows.map(([agent, r]) => (
          <div key={agent} className="usage-row">
            <span className="usage-name">{agent.replace("_", " ")}</span>
            <span className="usage-bar" title={`${num(r.p)} prompt + ${num(r.c)} completion`}>
              <span className="u-p" style={{ width: `${(r.p / max) * 100}%` }} />
              <span className="u-c" style={{ width: `${(r.c / max) * 100}%` }} />
            </span>
            <span className="usage-num mono">{num(r.p + r.c)}</span>
            <span className="usage-model">{r.model.replace(/^nvidia\//, "")}</span>
          </div>
        ))}
      </div>
      <div className="legend" style={{ marginTop: 0 }}><span><i style={{ background: "var(--sky)" }} />prompt</span><span><i style={{ background: "var(--teal)" }} />completion</span></div>
    </section>
  );
}

// ---------------- root cause ----------------

function RootCauseCard({ inc }: { inc: IncidentDetail }) {
  const rc = inc.root_cause;
  if (!rc) {
    return (
      <section className="panel panel-pad">
        <h2>Root cause</h2>
        <p className="muted" style={{ marginTop: 8 }}>
          {RUNNING.includes(inc.status) ? "The Investigator is reading the code." : "No root cause was recorded for this incident."}
        </p>
      </section>
    );
  }
  return (
    <section className="panel panel-pad stack" aria-label="Root cause">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>Root cause</h2>
        <div className="row">
          {rc.file && <span className="where">{rc.file}:{rc.line}{rc.function ? ` in ${rc.function}()` : ""}</span>}
          {rc.confidence && <span className={`confidence ${rc.confidence}`}>{rc.confidence} confidence</span>}
        </div>
      </div>
      <p className="cause">{rc.root_cause}</p>
      {rc.evidence?.length ? (
        <div>
          <h3 style={{ marginBottom: 6 }}>Evidence</h3>
          <ul className="evidence">{rc.evidence.map((e, i) => <li key={i}><Code text={e} /></li>)}</ul>
        </div>
      ) : null}
      {rc.fix_suggestion && <p className="small"><span className="faint">Suggested fix: </span>{rc.fix_suggestion}</p>}
      {rc.tavily_sources?.length ? (
        <details className="fold">
          <summary>{rc.tavily_sources.length} web source{rc.tavily_sources.length > 1 ? "s" : ""} checked</summary>
          <ul className="evidence" style={{ marginTop: 8 }}>
            {rc.tavily_sources.map((s, i) => <li key={i}><a href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a></li>)}
          </ul>
        </details>
      ) : null}
      {inc.similar_incidents?.length ? (
        <details className="fold">
          <summary>Learned from {inc.similar_incidents.length} past fix{inc.similar_incidents.length > 1 ? "es" : ""}</summary>
          <ul className="evidence" style={{ marginTop: 8 }}>
            {inc.similar_incidents.map((s) => (
              <li key={s.incident_id}>
                <Link to={`/incidents/${s.incident_id}`}>#{s.incident_id}</Link> {s.title}
                {s.pr_url && <>, <a href={s.pr_url} target="_blank" rel="noreferrer">PR</a></>}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

/** Render `backticked` spans as code. */
function Code({ text }: { text: string }) {
  return <>{text.split(/(`[^`]+`)/).map((p, i) => (p.startsWith("`") && p.endsWith("`") ? <code key={i}>{p.slice(1, -1)}</code> : p))}</>;
}

// ---------------- fix + tests ----------------

function attemptLabel(p: Patch) {
  if (p.kind === "revision") return `Revision ${Math.floor(p.attempt / 100)}`;
  return `Attempt ${p.attempt}`;
}

function FixCard({ inc }: { inc: IncidentDetail }) {
  const patches = inc.patches;
  const preferred = useMemo(() => {
    const verified = [...patches].reverse().find((p) => p.tests_passed);
    return (verified ?? patches[patches.length - 1])?.id;
  }, [patches]);
  const [sel, setSel] = useState<number | undefined>(preferred);
  const [tab, setTab] = useState<"diff" | "tests">("diff");
  useEffect(() => setSel(preferred), [preferred]);

  if (!patches.length) {
    return (
      <section className="panel panel-pad">
        <h2>Fix</h2>
        <p className="muted" style={{ marginTop: 8 }}>
          {RUNNING.includes(inc.status) ? "Waiting for the Fixer." : "No patch was produced."}
        </p>
      </section>
    );
  }
  const p = patches.find((x) => x.id === sel) ?? patches[patches.length - 1];

  return (
    <section className="panel" aria-label="Proposed fix">
      <div className="panel-pad stack" style={{ paddingBottom: 14 }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2>Proposed fix</h2>
          {patches.length > 1 && (
            <div className="attempts" aria-label="Attempts">
              {patches.map((x) => (
                <button key={x.id} className={`attempt-pill ${x.id === p.id ? "on" : ""}`} onClick={() => setSel(x.id)}>
                  <span className={x.tests_passed ? "pass" : x.tests_passed === false ? "fail" : "faint"}>{x.tests_passed ? "✓" : x.tests_passed === false ? "✗" : "…"}</span>
                  {attemptLabel(x)}
                </button>
              ))}
            </div>
          )}
        </div>
        {p.kind === "revision" && p.feedback && (
          <div className="alert info"><strong>Reviewer asked: </strong><Code text={p.feedback} /></div>
        )}
        <p>{p.explanation}</p>
      </div>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "diff"} className={`tab ${tab === "diff" ? "on" : ""}`} onClick={() => setTab("diff")}>Code change</button>
        <button role="tab" aria-selected={tab === "tests"} className={`tab ${tab === "tests" ? "on" : ""}`} onClick={() => setTab("tests")}>
          Test proof {p.tests_passed ? <span className="pass">✓</span> : p.tests_passed === false ? <span className="fail">✗</span> : null}
        </button>
      </div>
      {tab === "diff" ? (
        <DiffView diff={p.diff} />
      ) : (
        <div className="panel-pad">
          {p.test_summary ? <TestProof s={p.test_summary} patch={p} /> : <p className="muted">Tests haven't run for this attempt yet.</p>}
        </div>
      )}
    </section>
  );
}

// ---------------- agent timeline ----------------

const AGENT: Record<string, { name: string; mark: string }> = {
  detector: { name: "Detector", mark: "D" },
  investigator: { name: "Investigator", mark: "I" },
  fixer: { name: "Fixer", mark: "F" },
  tester: { name: "Tester", mark: "T" },
  approval_gate: { name: "Approval gate", mark: "A" },
};

function stepText(s: Step): string {
  const o = (s.output ?? {}) as Record<string, any>;
  if (o.error) return String(o.error);
  switch (s.agent) {
    case "detector": return o.summary ? `Summary: ${o.summary}` : o.note || `Parsed ${o.exception ?? "error"} at ${o.location?.file ?? "?"}:${o.location?.line ?? "?"} (${o.method ?? "rule-based"})`;
    case "investigator": return o.root_cause ? `${o.file}:${o.line}. ${o.root_cause}` : s.input_summary;
    case "fixer": return o.explanation ? `${o.explanation} (${o.code_lines ?? "?"} code lines, ${o.test_lines ?? 0} test lines)` : s.input_summary;
    case "tester": return o.before ? `${o.verified ? "Verified" : "Not verified"}: ${o.before.passed}/${o.before.passed + o.before.failed} passing before, ${o.after.passed}/${o.after.passed + o.after.failed} after (${o.runner})` : s.input_summary;
    default: return s.input_summary;
  }
}

function Timeline({ steps }: { steps: Step[] }) {
  return (
    <ol className="timeline">
      {steps.map((s) => {
        const a = AGENT[s.agent] ?? { name: s.agent, mark: "·" };
        const tokens = s.prompt_tokens + s.completion_tokens;
        return (
          <li key={s.id} className={`tl-item ${s.status}`}>
            <span className="tl-dot mono" aria-hidden="true">{s.status === "running" ? <span className="spinner" style={{ width: 13, height: 13, color: "var(--sky)" }} /> : a.mark}</span>
            <div>
              <div className="tl-head">
                <span className="tl-name">{a.name}{s.status === "error" && <span className="fail small"> failed</span>}</span>
                <span className="tl-meta">
                  {[s.model ? s.model.replace(/^nvidia\//, "") : "", tokens ? `${num(tokens)} tokens` : "", duration(s.started_at, s.finished_at)].filter(Boolean).join(", ")}
                </span>
              </div>
              <p className="tl-body">{stepText(s)}</p>
              {s.tool_calls?.length > 0 && (
                <div className="tools">
                  {s.tool_calls.map((t, i) => (
                    <details key={i} className="tool">
                      <summary><b>{t.tool}</b>({argText(t.args)})</summary>
                      {t.result_preview && <pre>{t.result_preview}</pre>}
                    </details>
                  ))}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function argText(args: Record<string, unknown>) {
  const s = Object.entries(args ?? {})
    .map(([k, v]) => `${k}=${typeof v === "string" ? JSON.stringify(v.length > 60 ? v.slice(0, 60) + "…" : v) : Array.isArray(v) ? `[${v.length}]` : JSON.stringify(v)}`)
    .join(", ");
  return s.length > 140 ? s.slice(0, 140) + "…" : s;
}

// ---------------- agent console ----------------

function consoleLines(inc: IncidentDetail): TermLine[] {
  const short = (v: string, n = 110) => (v.length > n ? v.slice(0, n) + "…" : v);
  // steps and human decisions interleaved in the order they happened
  type Item = { t: number; lines: TermLine[] };
  const items: Item[] = [];
  for (const s of inc.steps) {
    const out: TermLine[] = [];
    const a = AGENT[s.agent]?.name ?? s.agent;
    const model = s.model ? ` [${s.model.replace(/^nvidia\//, "")}]` : "";
    out.push({ id: `s${s.id}`, kind: "cmd", text: <><span className="t-agent">{a.toLowerCase().replace(" ", "_")}</span>{model}</>, ts: clock(s.started_at) });
    (s.tool_calls ?? []).forEach((t, i) => {
      out.push({ id: `s${s.id}c${i}`, kind: "call", text: <><span className="t-fn">{t.tool}</span>({argText(t.args)})</> });
      const first = (t.result_preview ?? "").split("\n").find((l) => l.trim());
      if (first) out.push({ id: `s${s.id}r${i}`, kind: "ret", text: short(first) });
    });
    const o = (s.output ?? {}) as Record<string, any>;
    if (s.status === "error" || o.error) out.push({ id: `s${s.id}e`, kind: "err", text: `✗ ${short(String(o.error ?? "failed"), 200)}` });
    else if (s.status === "running") out.push({ id: `s${s.id}w`, kind: "warn", text: s.agent === "approval_gate" ? "… waiting for a human decision" : "… working" });
    else out.push({ id: `s${s.id}d`, kind: s.agent === "tester" ? (o.verified ? "ok" : "err") : "ok", text: `✓ ${short(stepText(s), 200)}` });
    items.push({ t: new Date(s.started_at).getTime(), lines: out });
  }
  for (const d of inc.decisions) {
    items.push({ t: new Date(d.decided_at).getTime(), lines: [{ id: `d${d.id}`, kind: d.decision === "approve" ? "ok" : "err", text: `${d.decided_by} ${d.decision === "approve" ? "approved" : "rejected"}${d.reason ? `: "${d.reason}"` : ""}${d.pr_url ? `  → ${d.pr_url}` : ""}`, ts: clock(d.decided_at) }] });
  }
  for (const a of inc.audit) {
    if (a.action === "revision_requested" || a.action === "cancel_requested") {
      const text = a.action === "revision_requested" ? `${a.actor} asked for changes: "${short(String(a.detail?.feedback ?? ""), 120)}"` : `${a.actor} stopped the agents`;
      items.push({ t: new Date(a.ts).getTime(), lines: [{ id: `a${a.id}`, kind: "warn", text, ts: clock(a.ts) }] });
    }
  }
  items.sort((x, y) => x.t - y.t);
  const out: TermLine[] = [{ id: "h", kind: "cmd", text: `agentops run --incident ${inc.id}   # ${inc.exception_type} at ${inc.location?.file ?? "?"}:${inc.location?.line ?? "?"}`, ts: clock(inc.first_seen) }];
  items.forEach((i) => out.push(...i.lines));
  if (inc.status === "RESOLVED") out.push({ id: "m", kind: "ok", text: `✓ PR #${inc.pr_number} merged, incident resolved`, ts: clock(inc.resolved_at ?? undefined) });
  return out;
}

function AgentsCard({ inc }: { inc: IncidentDetail }) {
  const [view, setView] = useState<"console" | "timeline">("console");
  const lines = useMemo(() => consoleLines(inc), [inc]);
  return (
    <section className="panel" aria-label="What the agents did">
      <div className="panel-pad row" style={{ justifyContent: "space-between", paddingBottom: 0 }}>
        <h2>What the agents did</h2>
        <div className="seg" role="tablist">
          <button role="tab" aria-selected={view === "console"} className={view === "console" ? "on" : ""} onClick={() => setView("console")}>Console</button>
          <button role="tab" aria-selected={view === "timeline"} className={view === "timeline" ? "on" : ""} onClick={() => setView("timeline")}>Timeline</button>
        </div>
      </div>
      <div className="panel-pad">
        {view === "console" ? (
          <Terminal title={`incident-${inc.id}.log`} lines={lines} cursor={inc.running} maxHeight={520} />
        ) : inc.steps.length ? <Timeline steps={inc.steps} /> : <p className="muted">No steps yet.</p>}
      </div>
    </section>
  );
}

// ---------------- page ----------------

export default function IncidentPage() {
  const { id } = useParams();
  const { data: inc, error, mode } = useLive<IncidentDetail>(`/incidents/${id}/stream`, `/incidents/${id}`);

  if (!inc) {
    return error ? (
      <div className="stack"><Link to="/" className="crumb">Incidents</Link><ErrorBox message={error} /></div>
    ) : (
      <div className="empty"><span className="spinner" /> Loading incident #{id}</div>
    );
  }

  return (
    <>
      <header className="detail-head">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span className="crumb"><Link to="/">Incidents</Link> / #{inc.id}</span>
          <LiveDot mode={mode} />
        </div>
        <h1 className="detail-title">{inc.title}</h1>
        <div className="row">
          <StatusBadge status={inc.status} />
          <Sev level={inc.severity} />
          {inc.endpoint && <span className="chip mono">{inc.endpoint}</span>}
          {inc.occurrences > 1 && <span className="chip">{inc.occurrences}× seen</span>}
          {inc.regression_of && <Link className="chip warn" to={`/incidents/${inc.regression_of}`}>Came back after fix #{inc.regression_of}</Link>}
          <span className="small faint">First seen {ago(inc.first_seen)}</span>
        </div>
        {inc.summary && <p className="detail-summary">{inc.summary}</p>}
        {inc.status === "AWAITING_APPROVAL" && <a className="btn primary" href="#decision">Review proposed fix</a>}
      </header>

      <ErrorBox message={error} />

      <section className="panel panel-pad">
        <PipelineRail inc={inc} />
        {inc.status_reason && <p className="reason" style={{ marginTop: 16, textAlign: "center" }}>{inc.status_reason}</p>}
      </section>

      <div className="detail-grid">
        <div className="stack">
          <RegressionCard inc={inc} />
          <RootCauseCard inc={inc} />
          <FixCard inc={inc} />

          <AgentsCard inc={inc} />

          <section className="panel panel-pad stack">
            <details className="fold">
              <summary>Original traceback</summary>
              <pre className="log" style={{ marginTop: 10 }}>{inc.traceback}</pre>
            </details>
            <details className="fold">
              <summary>Audit log ({inc.audit.length} events)</summary>
              <ul className="testlist" style={{ marginTop: 10 }}>
                {inc.audit.map((a) => (
                  <li key={a.id}>
                    <span className="faint" style={{ whiteSpace: "nowrap" }}>{fmtTime(a.ts)}</span>
                    <span>{a.actor}: {a.action}{a.detail && Object.keys(a.detail).length ? ` ${JSON.stringify(a.detail).slice(0, 160)}` : ""}</span>
                  </li>
                ))}
              </ul>
            </details>
          </section>
        </div>

        <aside className="detail-aside">
          <DecisionPanel inc={inc} />
          <PrHistory inc={inc} />
          <TokenUsage inc={inc} />
          <section className="panel panel-pad">
            <dl className="kv">
              {inc.location?.file && <><dt>Crashed in</dt><dd className="mono small">{inc.location.file}:{inc.location.line}</dd></>}
              {inc.service && <><dt>Source</dt><dd>{inc.service}</dd></>}
              {inc.commit_sha && <><dt>Commit</dt><dd className="mono small">{inc.commit_sha.slice(0, 12)}</dd></>}
              <dt>Tokens</dt><dd>{num(inc.tokens_used)}</dd>
              <dt>Last seen</dt><dd>{fmtTime(inc.last_seen)}</dd>
              <dt>Signature</dt><dd className="mono small">{inc.error_signature}</dd>
            </dl>
          </section>
        </aside>
      </div>
    </>
  );
}
