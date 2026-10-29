import { FormEvent, useState } from "react";
import { ServerStatus, api, getApprover, getConfig, saveApprover, saveConfig, useApi } from "../api";
import { ErrorBox } from "../components/ui";
import { useMe } from "../auth";

function Yes({ on }: { on: boolean }) {
  return <span className={on ? "pass" : "faint"}>{on ? "Configured" : "Not configured"}</span>;
}

export default function SettingsPage() {
  const { me, signOut } = useMe();
  const signedIn = me.kind === "user";
  const cfg = getConfig();
  const [url, setUrl] = useState(cfg.url);
  const [key, setKey] = useState(cfg.key);
  const [approver, setApprover] = useState(getApprover());
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const { data: status } = useApi<ServerStatus>("/status");

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setMsg(null);
    saveConfig(url, key);
    saveApprover(approver);
    try {
      await api<ServerStatus>("/status");
      setMsg({ ok: true, text: "Connected. Settings saved in this browser." });
    } catch (err) {
      setMsg({ ok: false, text: (err as Error).message });
    } finally { setBusy(false); }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>Connect this dashboard to your AgentOps backend. These are stored only in this browser.</p>
        </div>
      </div>
      <div className="metric-grid" style={{ alignItems: "start" }}>
        <form className="panel panel-pad stack" onSubmit={save}>
          <h2>Connection</h2>
          <div className="field">
            <label htmlFor="c-url">Backend URL</label>
            <input id="c-url" className="input mono" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://localhost:8000" required />
          </div>
          <div className="field">
            <label htmlFor="c-key">Service API key (optional)</label>
            <input id="c-key" className="input mono" type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" />
            <span className="hint">One of the backend's API_KEYS. Only used when you're not signed in with an account.</span>
          </div>
          {!signedIn && (
            <div className="field">
              <label htmlFor="c-name">Your name for approvals</label>
              <input id="c-name" className="input" value={approver} onChange={(e) => setApprover(e.target.value)} placeholder="yasmeen" />
              <span className="hint">Shown on pull requests you approve.</span>
            </div>
          )}
          {msg && (msg.ok ? <div className="alert ok">{msg.text}</div> : <ErrorBox message={msg.text} />)}
          <div><button className="btn primary" disabled={busy}>{busy ? "Checking" : "Save and test connection"}</button></div>
        </form>

        <section className="panel panel-pad stack">
          <h2>Your account</h2>
          {signedIn && me.user ? (
            <dl className="kv">
              <dt>Name</dt><dd>{me.user.name}</dd>
              <dt>Email</dt><dd>{me.user.email}</dd>
              <dt>Role</dt><dd><span className={`role role-${me.role}`}>{me.role}</span></dd>
              {me.user.github_login && <><dt>GitHub</dt><dd>@{me.user.github_login}</dd></>}
            </dl>
          ) : (
            <p className="small muted">{me.kind === "key" ? "Connected with a service API key (full access)." : "Sign-in is off: no accounts or API keys exist yet."}</p>
          )}
          {me.kind !== "open" && <div><button className="btn sm" onClick={signOut}>Sign out</button></div>}
        </section>

        {status && (
          <section className="panel panel-pad stack">
            <h2>Backend</h2>
            <dl className="kv">
              <dt>LLM</dt><dd><Yes on={status.llm.configured} />, {status.llm.provider}</dd>
              <dt>Reasoning model</dt><dd className="mono small">{status.llm.reasoning_model}</dd>
              <dt>Triage model</dt><dd className="mono small">{status.llm.triage_model}</dd>
              <dt>Web search</dt><dd><Yes on={status.tavily.configured} /></dd>
              <dt>GitHub webhook</dt><dd><Yes on={status.webhooks.github} /></dd>
              <dt>Sentry webhook</dt><dd><Yes on={status.webhooks.sentry} /></dd>
              <dt>Email alerts</dt><dd><Yes on={status.notifications.email} /></dd>
              <dt>Slack alerts</dt><dd><Yes on={status.notifications.slack} /></dd>
              <dt>Default sandbox</dt><dd>{status.default_sandbox}</dd>
              <dt>Fix attempts</dt><dd>up to {status.max_fix_attempts}</dd>
              <dt>Regression test</dt><dd>{status.require_regression_test ? "Required for every fix" : "Optional"}</dd>
              <dt>Auth</dt><dd>{status.auth_enabled ? <span className="pass">API key required</span> : <span className="fail">Disabled: set API_KEYS</span>}</dd>
            </dl>
          </section>
        )}
      </div>
    </>
  );
}
