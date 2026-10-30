import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useIncidents } from "../App";
import { Incident, Project, RUNNING, Severity, Status, ago, useApi } from "../api";
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

function Counter({ value, label, tone, detail, selected, loading, onClick }: { value: number; label: string; tone: string; detail: string; selected: boolean; loading: boolean; onClick: () => void }) {
  const icon = tone === "run" ? "M3 12h4l3-8 4 16 3-8h4" : tone === "wait" ? "M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0" : tone === "good" ? "M6 7v10m0-10a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm0 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm12-14a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm0 0v5a5 5 0 0 1-5 5H8" : tone === "done" ? "m7 12 3 3 7-7M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0" : "M12 8v5m0 3v1M12 3 2 21h20L12 3Z";
  return <button className={`counter ${tone} ${value ? "has" : ""} ${selected ? "selected" : ""}`} onClick={onClick} type="button" aria-pressed={selected}>
    <span className="stat-heading"><span className="counter-label">{label}</span><span className="stat-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d={icon} /></svg></span></span>
    <span className="counter-value">{loading ? "—" : <CountUp value={value} />}</span>
    <span className="stat-detail"><i />{detail}</span>
  </button>;
}

export default function IncidentsPage() {
  const { data, error, mode, feed, changed } = useIncidents();
  const { data: projects } = useApi<Project[]>("/projects");
  const [filter, setFilter] = useState("all");
  const [projectId, setProjectId] = useState<number | null>(null);
  const nav = useNavigate();
  const [query, setQuery] = useState("");
  const [severity, setSeverity] = useState<Severity | "">("");
  const [sort, setSort] = useState("newest");

  const projectName = useMemo(() => {
    const m = new Map<number, string>();
    (projects ?? []).forEach((p) => m.set(p.id, p.name));
    return m;
  }, [projects]);

  const incidents = (data ?? []).filter((i) => projectId === null || i.project_id === projectId);
  const count = (k: string) => incidents.filter((i) => FILTERS.find((f) => f.key === k)!.match(i.status)).length;
  const waiting = incidents.filter((i) => i.status === "AWAITING_APPROVAL");
  const f = FILTERS.find((x) => x.key === filter)!;
  const search = query.trim().toLowerCase();
  const rows = incidents.filter((i) => f.match(i.status) && (!severity || i.severity === severity) &&
    (!search || [i.title, i.id, i.message, i.endpoint, i.service, projectName.get(i.project_id)].join(" ").toLowerCase().includes(search)))
    .sort((a, b) => sort === "oldest" ? Date.parse(a.last_seen) - Date.parse(b.last_seen) : sort === "severity" ?
      ["low", "medium", "high", "critical"].indexOf(b.severity) - ["low", "medium", "high", "critical"].indexOf(a.severity) : Date.parse(b.last_seen) - Date.parse(a.last_seen));
  const working = count("running");

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">OPERATIONS / OVERVIEW</div>
          <h1>Engineering overview</h1>
          <p>Your code, under watch. Your fixes, under control.</p>
        </div>
        <div className="row">
          {(projects?.length ?? 0) > 1 && (
            <select className="select" style={{ width: "min(190px, 100%)" }} value={projectId ?? ""} onChange={(e) => setProjectId(e.target.value ? Number(e.target.value) : null)} aria-label="Repository">
              <option value="">All repositories</option>
              {projects!.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          )}
          <Link className="btn primary" to="/send"><span aria-hidden="true">＋</span> Send test error</Link>
          <LiveDot mode={mode} />
        </div>
      </div>

      <ErrorBox message={error} />

      <section className="counters" aria-label="Incident summary">
        <Counter value={working} label="Active investigations" tone="run" detail="Agents at work" selected={filter === "running"} loading={data === null} onClick={() => setFilter("running")} />
        <Counter value={waiting.length} label="Pending approval" tone="wait" detail="Your review required" selected={filter === "approval"} loading={data === null} onClick={() => setFilter("approval")} />
        <Counter value={count("pr")} label="Open pull requests" tone="good" detail="Ready on GitHub" selected={filter === "pr"} loading={data === null} onClick={() => setFilter("pr")} />
        <Counter value={count("merged")} label="Resolved incidents" tone="done" detail="Fixes merged" selected={filter === "merged"} loading={data === null} onClick={() => setFilter("merged")} />
        <Counter value={count("attention")} label="Needs attention" tone="bad" detail="Manual follow-up" selected={filter === "attention"} loading={data === null} onClick={() => setFilter("attention")} />
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

      <div className="overview-grid">
      <section className="panel incident-panel">
        <div className="panel-head incident-list-heading"><div><h2><span className="section-marker" />Incident inbox</h2><p className="small muted">Every error. One place to take action.</p></div><span className="inbox-count">{data === null ? "Loading" : `${incidents.length} total`}</span></div>
        <div className="inbox-tools">
          <label className="incident-search"><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.7"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search incidents…" aria-label="Search incidents" />{query && <button onClick={() => setQuery("")} aria-label="Clear search">×</button>}</label>
          <select className="select" aria-label="Filter by severity" value={severity} onChange={(e) => setSeverity(e.target.value as Severity | "")}><option value="">All severities</option>{["critical", "high", "medium", "low"].map((v) => <option key={v} value={v}>{v[0].toUpperCase() + v.slice(1)}</option>)}</select>
          <select className="select" aria-label="Sort incidents" value={sort} onChange={(e) => setSort(e.target.value)}><option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="severity">Highest severity</option></select>
        </div>
        <div className="panel-head inbox-tabs">
          <div className="filters" role="group" aria-label="Filter incidents">
            {FILTERS.map((x) => (
              <button key={x.key} type="button" aria-pressed={filter === x.key} className={`filter ${filter === x.key ? "on" : ""}`} onClick={() => setFilter(x.key)}>
                {x.label} <span className="faint">{count(x.key)}</span>
              </button>
            ))}
          </div>
        </div>

        {data === null && !error ? (
          <div className="skeleton-list" aria-label="Loading incidents">{[0, 1, 2, 3].map((k) => <div key={k} className="skeleton" />)}</div>
        ) : rows.length === 0 ? (
          <div className="empty"><div className="empty-symbol" aria-hidden="true">✓</div>
            <h3>{error ? "Incident data unavailable" : incidents.length ? "No matching incidents" : "Your inbox starts here"}</h3>
            <p>{error ? "Check the backend connection in Settings, then try again." : incidents.length ? "Try a different search, severity, or status filter." : "Connect your first repository to start investigating errors and reviewing verified fixes."}</p>
            {incidents.length > 0 && <button className="btn" onClick={() => { setFilter("all"); setQuery(""); setSeverity(""); }}>Clear filters</button>}
            {error && <Link className="btn" to="/settings">Open settings</Link>}
            {!incidents.length && !error && (
              <div className="row" style={{ justifyContent: "center" }}>
                <Link className="btn" to="/projects">Add a repository</Link>
                <Link className="btn primary" to="/send">Send a test error</Link>
              </div>
            )}
          </div>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Incident / repository</th>
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
        <div className="inbox-footer"><span aria-live="polite">{data === null ? "Waiting for incident data" : `Showing ${rows.length} of ${incidents.length} incidents`}</span><span>Updates automatically <span aria-hidden="true">↻</span></span></div>
      </section>
      <aside className="overview-aside" aria-label="Agent operations">
        <section className="panel pipeline-panel">
          <div className="panel-head"><h2>Agent pipeline</h2><span className="micro-tag">HUMAN-GATED</span></div>
          <p className="pipeline-intro">Autonomous execution.<br /><strong>You're always in control.</strong></p>
          <ol className="agent-stages">
            {[{name:"Detector", text:"Groups incoming errors", statuses:["DETECTED"]}, {name:"Investigator", text:"Traces the root cause", statuses:["INVESTIGATING"]}, {name:"Fixer", text:"Builds a focused patch", statuses:["FIXING", "REVISING"]}, {name:"Tester", text:"Verifies the regression", statuses:["TESTING"]}].map((stage, n) => {
              const active = incidents.filter((i) => stage.statuses.includes(i.status)).length;
              return <li key={stage.name} className={active ? "stage-active" : ""}><span className="stage-node">0{n + 1}</span><span><strong>{stage.name}</strong><small>{stage.text}</small></span><span className="stage-count">{data === null ? "—" : active || "Idle"}</span></li>;
            })}
            <li className="approval-stage"><span className="stage-node">✓</span><span><strong>Human approval</strong><small>Review before any PR</small></span><button className="stage-count" onClick={() => setFilter("approval")} aria-label="Show incidents awaiting approval">{data === null ? "—" : waiting.length}</button></li>
          </ol>
          <Link className="pipeline-link" to="/activity">View agent activity <span aria-hidden="true">↗</span></Link>
        </section>
        <section className="repo-summary panel"><span className="repo-summary-icon" aria-hidden="true">⌘</span><h2>Your repositories</h2><p>{projects === null ? "Loading repositories…" : `${projects.length} connected ${projects.length === 1 ? "repository" : "repositories"}`}</p><Link to="/projects">Manage repositories <span aria-hidden="true">→</span></Link></section>
      </aside>
      </div>
      <section className="console-section" aria-label="Live incident events">
        <Terminal title={<><span className="console-prefix">agentops</span> / event stream <span className="faint">{working ? ` · ${working} running` : ""}</span></>} lines={feed} cursor maxHeight={220} />
      </section>
      <footer className="workspace-footer"><span>AGENTOPS <span className="faint">/ AI INCIDENT ENGINEERING</span></span><span><Link to="/help">Help</Link><Link to="/privacy">Privacy</Link><Link to="/terms">Terms</Link></span></footer>
    </>
  );
}
