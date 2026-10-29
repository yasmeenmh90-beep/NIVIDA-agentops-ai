import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useIncidents } from "../App";
import { Incident, Project, RUNNING, Status, ago, useApi } from "../api";
import { ErrorBox, LiveDot, Sev, StatusBadge } from "../components/ui";
import { CountUp, Terminal } from "../components/anim";

const FILTERS: { key: string; label: string; match: (s: Status) => boolean }[] = [
  { key: "all", label: "All", match: () => true },
  { key: "running", label: "Agents working", match: (s) => RUNNING.includes(s) },
  { key: "approval", label: "Needs approval", match: (s) => s === "AWAITING_APPROVAL" },
  { key: "pr", label: "PR open", match: (s) => s === "PR_CREATED" },
  { key: "merged", label: "Merged", match: (s) => s === "RESOLVED" },
  { key: "attention", label: "Needs attention", match: (s) => s === "NEEDS_HUMAN" || s === "FAILED" },
  { key: "closed", label: "Closed", match: (s) => s === "REJECTED" || s === "PR_CLOSED" },
];

function proofLine(i: Incident) {
  return i.status_reason || i.summary || i.message;
}

function Counter({ value, label, tone, onClick }: { value: number; label: string; tone: string; onClick: () => void }) {
  return (
    <button className={`counter ${tone} ${value ? "has" : ""}`} onClick={onClick} type="button">
      <span className="counter-value"><CountUp value={value} /></span>
      <span className="counter-label">{label}</span>
    </button>
  );
}

export default function IncidentsPage() {
  const { data, error, mode, feed, changed } = useIncidents();
  const { data: projects } = useApi<Project[]>("/projects");
  const [filter, setFilter] = useState("all");
  const [projectId, setProjectId] = useState<number | null>(null);
  const nav = useNavigate();

  const projectName = useMemo(() => {
    const m = new Map<number, string>();
    (projects ?? []).forEach((p) => m.set(p.id, p.name));
    return m;
  }, [projects]);

  const incidents = (data ?? []).filter((i) => projectId === null || i.project_id === projectId);
  const count = (k: string) => incidents.filter((i) => FILTERS.find((f) => f.key === k)!.match(i.status)).length;
  const waiting = incidents.filter((i) => i.status === "AWAITING_APPROVAL");
  const f = FILTERS.find((x) => x.key === filter)!;
  const rows = incidents.filter((i) => f.match(i.status));
  const working = count("running");

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">YOUR AI OPERATIONS CENTER</div>
          <h1>Incident overview<span className="title-dot">.</span></h1>
          <p>Monitor every incident. Review every fix. Keep your services moving.</p>
        </div>
        <div className="row">
          {(projects?.length ?? 0) > 1 && (
            <select className="select" style={{ width: 190 }} value={projectId ?? ""} onChange={(e) => setProjectId(e.target.value ? Number(e.target.value) : null)} aria-label="Repository">
              <option value="">All repositories</option>
              {projects!.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          )}
          <Link className="btn primary" to="/send"><span aria-hidden="true">＋</span> Send test error</Link>
          <LiveDot mode={mode} />
        </div>
      </div>

      <ErrorBox message={error} />

      <section className="ops-banner">
        <div><span className="eyebrow">AUTONOMOUS ENGINEERING · HUMAN CONTROL</span><h2>From error to verified fix.</h2><p>Your agents investigate, patch, and test. You make the final call.</p><Link to="/activity">Explore agent activity <span aria-hidden="true">↗</span></Link></div>
        <div className="workflow-preview" aria-label="Agent workflow">{["Detect", "Investigate", "Fix & test", "Your approval"].map((stage, index) => <div key={stage}><span>{index === 3 ? "✓" : `0${index + 1}`}</span><strong>{stage}</strong><small>{index === 3 ? "Human checkpoint" : "AI agent"}</small></div>)}</div>
      </section>
      <section className="hero-ops">
        <div className="counters">
          <Counter value={working} label="agents working" tone="run" onClick={() => setFilter("running")} />
          <Counter value={waiting.length} label="waiting for you" tone="wait" onClick={() => setFilter("approval")} />
          <Counter value={count("pr")} label="pull requests open" tone="good" onClick={() => setFilter("pr")} />
          <Counter value={count("merged")} label="fixes merged" tone="done" onClick={() => setFilter("merged")} />
          <Counter value={count("attention")} label="need a human" tone="bad" onClick={() => setFilter("attention")} />
        </div>
        <Terminal
          title={<>live feed <span className="faint">{working ? `(${working} running)` : ""}</span></>}
          lines={feed}
          cursor
          maxHeight={210}
        />
      </section>

      {waiting.length > 0 && (
        <section className="queue" aria-label="Waiting for your approval">
          <h2>Waiting for you</h2>
          {waiting.map((i) => (
            <Link key={i.id} to={`/incidents/${i.id}`} className="queue-card">
              <span className="queue-id">#{i.id}</span>
              <span>
                <span className="queue-title">{i.title}</span>
                <span className="queue-proof" style={{ display: "block" }}>{proofLine(i)}</span>
              </span>
              <span className="btn amber sm">Review fix</span>
            </Link>
          ))}
        </section>
      )}

      <section className="panel incident-panel">
        <div className="panel-head incident-list-heading"><div><h2>Incident inbox</h2><p className="small muted">Track investigations and review proposed fixes.</p></div><span className="inbox-count">{incidents.length} total</span></div>
        <div className="panel-head">
          <div className="filters" role="tablist" aria-label="Filter incidents">
            {FILTERS.map((x) => (
              <button key={x.key} role="tab" aria-selected={filter === x.key} className={`filter ${filter === x.key ? "on" : ""}`} onClick={() => setFilter(x.key)}>
                {x.label} <span className="faint">{count(x.key)}</span>
              </button>
            ))}
          </div>
        </div>

        {data === null && !error ? (
          <div className="skeleton-list" aria-label="Loading incidents">{[0, 1, 2, 3].map((k) => <div key={k} className="skeleton" />)}</div>
        ) : rows.length === 0 ? (
          <div className="empty"><div className="empty-symbol" aria-hidden="true">✓</div>
            <h3>{incidents.length ? "No incidents match this filter" : "No incidents yet"}</h3>
            <p>{incidents.length ? "Try another filter." : "Register a repository, then send an error from your app or try a test error."}</p>
            {!incidents.length && (
              <div className="row" style={{ justifyContent: "center" }}>
                <Link className="btn" to="/projects">Add a repository</Link>
                <Link className="btn primary" to="/send">Send a test error</Link>
              </div>
            )}
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Error</th>
                  <th>Status</th>
                  <th className="hide-sm">Severity</th>
                  <th className="hide-sm">Last seen</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((i, n) => (
                  <tr key={`${i.id}-${changed[i.id] ?? 0}`} onClick={() => nav(`/incidents/${i.id}`)} className={`row-in ${changed[i.id] ? "row-flash" : ""}`} style={{ animationDelay: `${Math.min(n, 12) * 35}ms` }}>
                    <td className="idcell">#{i.id}</td>
                    <td>
                      <Link to={`/incidents/${i.id}`} className="inc-title" onClick={(e) => e.stopPropagation()}>{i.title}</Link>
                      <span className="inc-sub">
                        {projectName.get(i.project_id) && <span>{projectName.get(i.project_id)}</span>}
                        {i.endpoint && <span className="mono">{i.endpoint}</span>}
                        {i.occurrences > 1 && <span className="chip">{i.occurrences}× seen</span>}
                        {i.regression_of && <span className="chip warn">Came back after fix #{i.regression_of}</span>}
                        {i.pr_url && <a href={i.pr_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>PR #{i.pr_number}</a>}
                      </span>
                    </td>
                    <td><StatusBadge status={i.status} /></td>
                    <td className="hide-sm"><Sev level={i.severity} /></td>
                    <td className="hide-sm muted small" title={i.last_seen}>{ago(i.last_seen)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
