import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ActivityEvent, Project, STATUS_LABEL, Status, api, ago, useApi, useConfigVersion } from "../api";
import { ErrorBox, StatusBadge } from "../components/ui";

function describe(a: ActivityEvent): { text: string; tone: string } {
  const d = a.detail ?? {};
  switch (a.action) {
    case "incident_opened": return { text: `opened from ${d.source || "logs"}`, tone: "warn" };
    case "status_change": {
      const to = d.to as Status;
      const tone = to === "AWAITING_APPROVAL" || to === "PR_CREATED" || to === "RESOLVED" ? "ok" : to === "FAILED" || to === "NEEDS_HUMAN" ? "err" : "run";
      return { text: `${STATUS_LABEL[d.from as Status] ?? d.from} → ${STATUS_LABEL[to] ?? to}${d.reason ? `: ${d.reason}` : ""}`, tone };
    }
    case "repo_synced": return { text: `synced ${d.project} at ${String(d.sha ?? "").slice(0, 10)}`, tone: "dim" };
    case "similar_incidents": return { text: `recalled past fixes ${(d.ids ?? []).map((x: number) => `#${x}`).join(", ")}`, tone: "dim" };
    case "approve_requested": return { text: "approval started", tone: "ok" };
    case "pr_created": return { text: `opened PR ${d.pr_url ?? ""}`, tone: "ok" };
    case "pr_failed": return { text: `could not open PR: ${d.error ?? ""}`, tone: "err" };
    case "revision_requested": return { text: `asked for a revision (${d.source}): ${d.feedback ?? ""}`, tone: "warn" };
    case "revision_ignored": return { text: `revision ignored: ${d.reason ?? ""}`, tone: "dim" };
    case "cancel_requested": return { text: `stopped the agents while ${STATUS_LABEL[d.status as Status] ?? d.status}`, tone: "err" };
    default:
      if (a.action.startsWith("tool:")) {
        const args = d.args ? Object.entries(d.args).map(([k, v]) => `${k}=${typeof v === "string" ? JSON.stringify(v.slice(0, 50)) : JSON.stringify(v)}`).join(", ") : "";
        return { text: `${a.action.slice(5)}(${args})`, tone: "tool" };
      }
      return { text: `${a.action} ${JSON.stringify(d).slice(0, 120)}`, tone: "dim" };
  }
}

export default function ActivityPage() {
  const version = useConfigVersion();
  const { data: projects } = useApi<Project[]>("/projects");
  const [project, setProject] = useState("");
  const [tools, setTools] = useState(false);
  const [items, setItems] = useState<ActivityEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [more, setMore] = useState(true);
  const [loading, setLoading] = useState(false);

  const query = useCallback((before?: number) => {
    const q = new URLSearchParams({ limit: "80", include_tools: String(tools) });
    if (project) q.set("project", project);
    if (before) q.set("before_id", String(before));
    return api<ActivityEvent[]>(`/activity?${q}`);
  }, [project, tools]);

  // first page + refresh every 4 s (merging new events on top)
  useEffect(() => {
    let alive = true;
    setItems([]); setMore(true);
    const load = () => query().then((rows) => {
      if (!alive) return;
      setError(null);
      setItems((cur) => {
        const known = new Set(cur.map((x) => x.id));
        const fresh = rows.filter((r) => !known.has(r.id));
        return cur.length ? [...fresh, ...cur] : rows;
      });
    }).catch((e: Error) => alive && setError(e.message));
    load();
    const t = setInterval(load, 4000);
    return () => { alive = false; clearInterval(t); };
  }, [query, version]);

  const loadMore = async () => {
    if (!items.length) return;
    setLoading(true);
    try {
      const rows = await query(items[items.length - 1].id);
      setItems((cur) => [...cur, ...rows]);
      if (rows.length < 80) setMore(false);
    } catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Activity</h1>
          <p>Everything that happened across all incidents: agent decisions, status changes, approvals, PRs and revisions.</p>
        </div>
        <div className="row">
          <select className="select" style={{ width: 200 }} value={project} onChange={(e) => setProject(e.target.value)} aria-label="Repository">
            <option value="">All repositories</option>
            {(projects ?? []).map((p) => <option key={p.id} value={p.name}>{p.name}</option>)}
          </select>
          <label className="toggle">
            <input type="checkbox" checked={tools} onChange={(e) => setTools(e.target.checked)} />
            <span>Show tool calls</span>
          </label>
        </div>
      </div>
      <ErrorBox message={error} />
      <section className="panel">
        {items.length === 0 && !error ? (
          <div className="skeleton-list">{[0, 1, 2, 3, 4].map((k) => <div key={k} className="skeleton" style={{ height: 38 }} />)}</div>
        ) : (
          <ol className="activity">
            {items.map((a) => {
              const { text, tone } = describe(a);
              return (
                <li key={a.id} className={`act act-${tone}`}>
                  <span className="act-time" title={a.ts}>{ago(a.ts)}</span>
                  <span className="act-actor">{a.actor}</span>
                  <span className="act-text">{text}</span>
                  <Link className="act-inc" to={`/incidents/${a.incident_id}`} title={a.incident_title}>
                    #{a.incident_id} <span className="faint">{a.project}</span>
                  </Link>
                  <span className="act-status"><StatusBadge status={a.incident_status} /></span>
                </li>
              );
            })}
          </ol>
        )}
        {items.length > 0 && more && (
          <div className="panel-pad" style={{ textAlign: "center" }}>
            <button className="btn sm" onClick={loadMore} disabled={loading}>{loading ? "Loading" : "Load older events"}</button>
          </div>
        )}
      </section>
    </>
  );
}
