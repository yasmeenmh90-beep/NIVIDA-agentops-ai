import { useState } from "react";
import { Link } from "react-router-dom";
import { Project, ServerStatus, api, getAlertPrefs, getConfig, saveAlertPrefs, useApi } from "../api";
import { beep, canNotify, requestDesktopPermission } from "../alerts";
import { ErrorBox } from "../components/ui";
import { useMe } from "../auth";

export function CopyField({ value, label, multiline = false }: { value: string; label?: string; multiline?: boolean }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1400); }
    catch { /* clipboard blocked: the text is selectable */ }
  };
  return (
    <div className="copy">
      {label && <span className="copy-label">{label}</span>}
      <div className={`copy-box ${multiline ? "multi" : ""}`}>
        <code>{value}</code>
        <button className={`btn sm ${copied ? "primary" : ""}`} onClick={copy} type="button">{copied ? "Copied" : "Copy"}</button>
      </div>
    </div>
  );
}

function On({ on, children }: { on: boolean; children: string }) {
  return <span className={`status ${on ? "good" : ""}`}><i />{children}: {on ? "on" : "off"}</span>;
}

function AlertsCard() {
  const [prefs, setPrefs] = useState(getAlertPrefs());
  const [perm, setPerm] = useState<string>(canNotify() ? Notification.permission : "unsupported");
  const update = (p: typeof prefs) => { setPrefs(p); saveAlertPrefs(p); };
  const toggleDesktop = async () => {
    if (!prefs.desktop) {
      const r = await requestDesktopPermission();
      setPerm(r);
      if (r !== "granted") return;
    }
    update({ ...prefs, desktop: !prefs.desktop });
  };
  return (
    <section className="panel panel-pad stack">
      <div>
        <h2>Alerts in this browser</h2>
        <p className="small muted" style={{ marginTop: 4 }}>
          While the dashboard is open, get a desktop notification and a short sound when a fix needs your approval, an incident fails, or a fix is merged.
        </p>
      </div>
      <label className="toggle">
        <input type="checkbox" checked={prefs.desktop} onChange={toggleDesktop} disabled={perm === "unsupported" || perm === "denied"} />
        <span>Desktop notifications</span>
      </label>
      {perm === "denied" && <p className="small fail">Notifications are blocked for this site. Allow them in your browser's site settings.</p>}
      {perm === "unsupported" && <p className="small faint">This browser doesn't support desktop notifications.</p>}
      <label className="toggle">
        <input type="checkbox" checked={prefs.sound} onChange={() => update({ ...prefs, sound: !prefs.sound })} />
        <span>Sound</span>
      </label>
      <div><button className="btn sm" onClick={() => beep("attention")} type="button">Play the alert sound</button></div>
    </section>
  );
}

function ChannelsCard({ status }: { status: ServerStatus }) {
  const { canAct } = useMe();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, string> | null>(null);
  const test = async () => {
    setBusy(true); setError(null); setResults(null);
    try { setResults((await api<{ results: Record<string, string> }>("/notifications/test", { method: "POST" })).results); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const n = status.notifications;
  return (
    <section className="panel panel-pad stack">
      <div>
        <h2>Team alerts</h2>
        <p className="small muted" style={{ marginTop: 4 }}>
          Sent by the backend for every incident, whether or not anyone has the dashboard open. Channels are set in <code>backend/.env</code>.
        </p>
      </div>
      <div className="row">
        <On on={n.email}>Email</On>
        <On on={n.slack}>Slack</On>
        <On on={n.webhook}>Webhook</On>
      </div>
      {status.notify_min_severity && <p className="small faint">New-incident alerts from severity {status.notify_min_severity} and up.</p>}
      <div>
        <button className="btn" onClick={test} disabled={!canAct || busy || !(n.email || n.slack || n.webhook)} type="button">
          {busy ? <><span className="spinner" /> Sending</> : "Send a test alert"}
        </button>
      </div>
      {results && (
        <ul className="testlist">
          {Object.entries(results).map(([k, v]) => <li key={k}><span className={v === "ok" ? "pass" : "fail"}>{v === "ok" ? "✓" : "✗"}</span>{k}: {v}</li>)}
        </ul>
      )}
      <ErrorBox message={error} />
    </section>
  );
}

function ProjectSetup({ p, base, status }: { p: Project; base: string; status: ServerStatus }) {
  const [tab, setTab] = useState<"sentry" | "github" | "python" | "curl">("sentry");
  const { key } = getConfig();
  const handler = `import agentops_handler

agentops_handler.install(
    url="${base}",
    api_key="YOUR_API_KEY",
    project="${p.name}",
    service="${p.name}-api",
)`;
  const curl = `curl -X POST ${base}/ingest/logs \\
  -H "X-API-Key: YOUR_API_KEY" -H "Content-Type: application/json" \\
  -d '{"project": "${p.name}", "source": "prod", "lines": ["...traceback lines..."]}'`;
  return (
    <section className="panel">
      <div className="panel-head">
        <h2 className="mono">{p.name}</h2>
        <div className="seg" role="tablist">
          {(["sentry", "github", "python", "curl"] as const).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
              {{ sentry: "Sentry", github: "GitHub", python: "Python app", curl: "Any language" }[t]}
            </button>
          ))}
        </div>
      </div>
      <div className="panel-pad stack">
        {tab === "sentry" && (
          <>
            <div className="row"><On on={status.webhooks.sentry}>Signature check</On></div>
            <CopyField label="Webhook URL" value={`${base}/webhooks/sentry/${p.name}`} />
            <ol className="steps">
              <li>In Sentry go to <b>Settings → Developer Settings → Custom Integrations → Create New Integration → Internal Integration</b>.</li>
              <li>Paste the webhook URL above, turn on <b>Alert Rule Action</b>, and set <b>Issue &amp; Event</b> permission to <b>Read</b>. Save.</li>
              <li>Copy the <b>Client Secret</b> into <code>SENTRY_CLIENT_SECRET</code> in <code>backend/.env</code> and restart the backend.</li>
              <li>Create an issue alert: <b>A new issue is created</b> → <b>Send a notification via AgentOps</b>.</li>
            </ol>
          </>
        )}
        {tab === "github" && (
          <>
            <div className="row"><On on={status.webhooks.github}>Signature check</On>{p.github_repo && <a href={`https://github.com/${p.github_repo}/settings/hooks/new`} target="_blank" rel="noreferrer" className="btn sm">Open webhook settings on GitHub</a>}</div>
            <CopyField label="Payload URL" value={`${base}/webhooks/github`} />
            <CopyField label="Content type" value="application/json" />
            <ol className="steps">
              <li>Secret: the value of <code>GITHUB_WEBHOOK_SECRET</code> in <code>backend/.env</code>.</li>
              <li>Events: <b>Pull requests</b>, <b>Pull request reviews</b> and <b>Issue comments</b>.</li>
              <li>Then a merge marks the incident resolved, and a “Request changes” review or an <code>/agentops …</code> comment makes the agent revise the PR (up to {status.max_revisions ?? 3} times).</li>
            </ol>
          </>
        )}
        {tab === "python" && (
          <>
            <p className="small muted">Copy <code>clients/python/agentops_handler.py</code> into your service, then call this once at startup. It sends exceptions in the background and never blocks your app.</p>
            <CopyField value={handler} multiline />
          </>
        )}
        {tab === "curl" && (
          <>
            <p className="small muted">Any service can post log lines that contain a Python traceback.</p>
            <CopyField value={curl} multiline />
          </>
        )}
        {!key && <p className="small faint">Replace YOUR_API_KEY with one of the backend's API_KEYS.</p>}
      </div>
    </section>
  );
}

export default function IntegrationsPage() {
  const { data: status, error } = useApi<ServerStatus>("/status");
  const { data: projects } = useApi<Project[]>("/projects");
  const base = (status?.public_base_url || getConfig().url).replace(/\/+$/, "");
  const local = /localhost|127\.0\.0\.1/.test(base);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Integrations</h1>
          <p>Connect error sources, GitHub and alerts. Every URL below is ready to copy.</p>
        </div>
      </div>
      <ErrorBox message={error} />
      {status && (
        <div className="stack">
          {local && (
            <div className="alert info">
              Your backend URL is local, so Sentry and GitHub can't reach it. Run <code>cloudflared tunnel --url http://localhost:8000</code> and set <code>PUBLIC_BASE_URL</code> in <code>backend/.env</code> to the tunnel URL (or deploy the backend).
            </div>
          )}
          <div className="metric-grid" style={{ alignItems: "start" }}>
            <ChannelsCard status={status} />
            <AlertsCard />
          </div>
          <h2 style={{ marginTop: 8 }}>Per repository</h2>
          {projects && projects.length === 0 && <div className="alert info">No repositories yet. <Link to="/projects">Add one first</Link>.</div>}
          {(projects ?? []).map((p) => <ProjectSetup key={p.id} p={p} base={base} status={status} />)}
        </div>
      )}
    </>
  );
}
