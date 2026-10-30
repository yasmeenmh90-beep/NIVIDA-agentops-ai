import { useState } from "react";
import { Metrics, Project, STATUS_LABEL, Status, TimingStat, num, pct, useApi } from "../api";
import { ErrorBox } from "../components/ui";
import { CountUp, Gauge } from "../components/anim";

const STATUS_COLOR: Record<string, string> = {
  RESOLVED: "var(--violet)", PR_CREATED: "var(--teal)", AWAITING_APPROVAL: "var(--amber)",
  REVISING: "var(--sky)", INVESTIGATING: "var(--sky)", FIXING: "var(--sky)", TESTING: "var(--sky)", DETECTED: "var(--sky)",
  NEEDS_HUMAN: "var(--coral)", FAILED: "#c4504d", REJECTED: "var(--faint)", PR_CLOSED: "#4a5d78",
};
const SEV_COLOR: Record<string, string> = { low: "var(--muted)", medium: "var(--sky)", high: "var(--amber)", critical: "var(--coral)" };

function RateUp({ v }: { v: number | null }) {
  return v === null ? <>{pct(v)}</> : <CountUp value={v * 100} format={(n) => `${Math.round(n)}%`} />;
}

function minutes(v: number | null) {
  if (v === null) return "—";
  if (v < 1) return `${Math.round(v * 60)} s`;
  if (v < 60) return `${v.toFixed(1)} min`;
  return `${(v / 60).toFixed(1)} h`;
}

function Split({ data, colors, labels }: { data: Record<string, number>; colors: Record<string, string>; labels?: Record<string, string> }) {
  const total = Object.values(data).reduce((a, b) => a + b, 0) || 1;
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  return (
    <>
      <div className="bar" role="img" aria-label={entries.map(([k, v]) => `${labels?.[k] ?? k}: ${v}`).join(", ")}>
        {entries.map(([k, v]) => <span key={k} style={{ width: `${(v / total) * 100}%`, background: colors[k] ?? "var(--faint)" }} />)}
      </div>
      <div className="legend">
        {entries.map(([k, v]) => <span key={k}><i style={{ background: colors[k] ?? "var(--faint)" }} />{labels?.[k] ?? k} {v}</span>)}
      </div>
    </>
  );
}

function Timing({ label, t }: { label: string; t: TimingStat }) {
  return (
    <div className="panel stat">
      <div className="stat-value">{minutes(t.median)}</div>
      <div className="stat-label">{label}, median</div>
      <div className="small faint" style={{ marginTop: 8 }}>{t.count ? `p90 ${minutes(t.p90)} across ${t.count}` : "No data yet"}</div>
    </div>
  );
}

export default function MetricsPage() {
  const [project, setProject] = useState("");
  const { data: projects } = useApi<Project[]>("/projects");
  const { data: m, error } = useApi<Metrics>(`/metrics${project ? `?project=${encodeURIComponent(project)}` : ""}`);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Metrics</h1>
          <p>How often the agents produce a fix that passes verification, and how fast.</p>
        </div>
        <select className="select" style={{ width: "min(220px, 100%)" }} value={project} onChange={(e) => setProject(e.target.value)} aria-label="Repository">
          <option value="">All repositories</option>
          {(projects ?? []).map((p) => <option key={p.id} value={p.name}>{p.name}</option>)}
        </select>
      </div>
      <ErrorBox message={error} />
      {m && (
        <div className="stack">
          <div className="hero-metric">
            <div className="panel stat stat-big">
              <Gauge value={m.fix_quality.verified_fix_rate} label="Verified fix rate" />
              <div>
                <div className="stat-title">Verified fix rate</div>
                <div className="stat-label">of incidents that reached the Fixer got a fix whose regression test failed before and passed after, with no regressions</div>
              </div>
            </div>
            <div className="panel stat">
              <div className="stat-value"><RateUp v={m.fix_quality.first_attempt_success_rate} /></div>
              <div className="stat-label">verified on the first attempt</div>
            </div>
            <div className="panel stat">
              <div className="stat-value"><RateUp v={m.fix_quality.approval_rate} /></div>
              <div className="stat-label">of decided fixes approved by a human</div>
            </div>
          </div>

          <div className="metric-grid">
            <Timing label="Error to verified fix" t={m.timing_minutes.detect_to_verified_fix} />
            <Timing label="Error to pull request" t={m.timing_minutes.detect_to_pr} />
            <Timing label="Error to merged" t={m.timing_minutes.detect_to_merged_mttr} />
          </div>

          <div className="metric-grid">
            <div className="panel panel-pad">
              <h3 style={{ marginBottom: 12 }}>{num(m.incidents.total)} incidents by status</h3>
              <Split data={m.incidents.by_status} colors={STATUS_COLOR} labels={STATUS_LABEL as Record<Status, string>} />
            </div>
            <div className="panel panel-pad">
              <h3 style={{ marginBottom: 12 }}>By severity</h3>
              <Split data={m.incidents.by_severity} colors={SEV_COLOR} />
            </div>
          </div>

          <div className="metric-grid">
            <div className="panel stat"><div className="stat-value"><CountUp value={m.incidents.total_occurrences} /></div><div className="stat-label">error occurrences grouped</div></div>
            <div className="panel stat"><div className="stat-value"><CountUp value={m.fix_quality.merged} /></div><div className="stat-label">fixes merged</div></div>
            <div className="panel stat"><div className="stat-value">{m.fix_quality.revisions.verified}/{m.fix_quality.revisions.total}</div><div className="stat-label">reviewer revisions verified</div></div>
            <div className="panel stat"><div className="stat-value"><CountUp value={m.incidents.regressions} /></div><div className="stat-label">errors that came back after a fix</div></div>
            <div className="panel stat"><div className="stat-value"><CountUp value={m.fix_quality.needs_human_now} /></div><div className="stat-label">waiting on a human to fix by hand</div></div>
            <div className="panel stat"><div className="stat-value"><CountUp value={m.llm_tokens.total} /></div><div className="stat-label">Nemotron tokens{m.llm_tokens.per_incident.median ? `, ${num(Math.round(m.llm_tokens.per_incident.median))} per incident` : ""}</div></div>
          </div>
        </div>
      )}
    </>
  );
}
