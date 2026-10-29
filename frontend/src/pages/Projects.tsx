import { FormEvent, useEffect, useState } from "react";
import { Project, api, ago, useApi } from "../api";
import { ErrorBox } from "../components/ui";
import { useMe } from "../auth";
import { isRepoUrl } from "../validate";

const SETUP_TONE: Record<string, string> = { ready: "good", failed: "bad", running: "run", pending: "run" };
const SETUP_LABEL: Record<string, string> = { ready: "Ready", failed: "Setup failed", running: "Installing", pending: "Queued" };

const EDIT_FIELDS: { k: keyof Project; label: string; hint?: string; mono?: boolean }[] = [
  { k: "default_branch", label: "Branch" },
  { k: "app_path", label: "App folder", hint: "Subfolder with the Python app (monorepos)." },
  { k: "test_command", label: "Test command", mono: true, hint: "Must be pytest. Empty uses the default." },
  { k: "install_command", label: "Install command", mono: true, hint: "Runs in the project's venv. Empty installs requirements*.txt." },
  { k: "github_repo", label: "GitHub repo", mono: true, hint: "owner/name for pull requests. Empty turns PRs off." },
  { k: "sandbox_image", label: "Docker image", mono: true, hint: "Needed for the Docker sandbox." },
  { k: "protected_paths", label: "Protected paths", mono: true, hint: "Comma-separated paths the agent may never change." },
];

function EditForm({ p, onDone }: { p: Project; onDone: (changed: boolean) => void }) {
  const [f, setF] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = { sandbox_mode: p.sandbox_mode, python_env: p.python_env };
    EDIT_FIELDS.forEach(({ k }) => { init[k] = String(p[k] ?? ""); });
    return init;
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    const body: Record<string, string> = {};
    Object.entries(f).forEach(([k, v]) => { if (v !== String((p as unknown as Record<string, unknown>)[k] ?? "")) body[k] = v; });
    try {
      if (Object.keys(body).length) await api(`/projects/${p.name}`, { method: "PATCH", body });
      onDone(Object.keys(body).length > 0);
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };
  return (
    <form className="stack edit-form" onSubmit={save}>
      <div className="form-grid">
        {EDIT_FIELDS.map(({ k, label, hint, mono }) => (
          <div className="field" key={k}>
            <label htmlFor={`e-${p.id}-${k}`}>{label}</label>
            <input id={`e-${p.id}-${k}`} className={`input ${mono ? "mono" : ""}`} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} />
            {hint && <span className="hint">{hint}</span>}
          </div>
        ))}
        <div className="field">
          <label htmlFor={`e-${p.id}-sb`}>Sandbox</label>
          <select id={`e-${p.id}-sb`} className="select" value={f.sandbox_mode} onChange={(e) => setF({ ...f, sandbox_mode: e.target.value })}>
            <option value="">Server default</option><option value="subprocess">Subprocess</option>
            <option value="docker">Docker (no network)</option><option value="auto">Auto</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor={`e-${p.id}-env`}>Python environment</label>
          <select id={`e-${p.id}-env`} className="select" value={f.python_env} onChange={(e) => setF({ ...f, python_env: e.target.value })}>
            <option value="venv">Own virtualenv</option><option value="system">Server interpreter</option>
          </select>
        </div>
      </div>
      <ErrorBox message={error} />
      <div className="row">
        <button className="btn primary sm" disabled={busy}>{busy ? "Saving" : "Save changes"}</button>
        <button className="btn ghost sm" type="button" onClick={() => onDone(false)}>Cancel</button>
        <span className="small faint">Changing the branch, folder or install command re-runs setup.</span>
      </div>
    </form>
  );
}

function ProjectCard({ p, onChanged }: { p: Project; onChanged: () => void }) {
  const { canAct } = useMe();
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [log, setLog] = useState<string | null>(null);
  const [detail, setDetail] = useState<Project | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadDetail = async () => {
    try {
      const d = await api<Project>(`/projects/${p.name}`);
      setDetail(d);
      setLog(d.setup_log ?? "");
    } catch (e) { setError((e as Error).message); }
  };
  const remove = async () => {
    setBusy(true); setError(null);
    try { await api(`/projects/${p.name}`, { method: "DELETE" }); onChanged(); }
    catch (e) { setError((e as Error).message); setConfirmDelete(false); }
    finally { setBusy(false); }
  };
  const resetup = async () => {
    setBusy(true); setError(null);
    try { await api(`/projects/${p.name}/setup`, { method: "POST" }); onChanged(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  return (
    <article className={`panel panel-pad stack ${editing ? "proj-editing" : ""}`}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 className="mono">{p.name}</h2>
        <span className={`status ${SETUP_TONE[p.setup_status] ?? ""}`}><i />{SETUP_LABEL[p.setup_status] ?? p.setup_status}</span>
      </div>
      <dl className="kv">
        <dt>Repository</dt>
        <dd>{p.github_repo ? <a href={`https://github.com/${p.github_repo}`} target="_blank" rel="noreferrer">{p.github_repo}</a> : p.repo_url}</dd>
        <dt>Branch</dt><dd>{p.default_branch}{p.app_path ? ` in ${p.app_path}/` : ""}</dd>
        {p.head_sha && <><dt>At commit</dt><dd className="mono small">{p.head_sha.slice(0, 12)}{p.synced_at ? `, synced ${ago(p.synced_at)}` : ""}</dd></>}
        <dt>Tests</dt><dd className="mono small">{detail?.effective_test_command || p.test_command || "default pytest"}</dd>
        <dt>Sandbox</dt><dd>{detail?.effective_sandbox || p.sandbox_mode || "server default"}{p.sandbox_image ? ` (${p.sandbox_image})` : ""}</dd>
        {detail && <><dt>GitHub token</dt><dd className={detail.github_token_present ? "pass" : "fail"}>{detail.github_token_present ? `Found in ${detail.github_token_env}` : `Missing: set ${detail.github_token_env}`}</dd></>}
      </dl>
      {editing ? (
        <EditForm p={p} onDone={(changed) => { setEditing(false); if (changed) { setDetail(null); onChanged(); } }} />
      ) : (
        <div className="row">
          <button className="btn sm" onClick={() => (log === null ? loadDetail() : setLog(null))}>{log === null ? "Setup log" : "Hide log"}</button>
          {canAct && <button className="btn sm" onClick={() => setEditing(true)}>Edit settings</button>}
          {canAct && <button className="btn sm ghost" disabled={busy || p.setup_status === "running"} onClick={resetup}>{busy ? "Starting" : "Sync and reinstall"}</button>}
          {!canAct ? null : !confirmDelete ? (
            <button className="btn sm danger" onClick={() => setConfirmDelete(true)}>Delete</button>
          ) : (
            <span className="row confirm">
              <span className="small">Delete {p.name}?</span>
              <button className="btn sm danger" disabled={busy} onClick={remove}>Yes, delete</button>
              <button className="btn sm ghost" onClick={() => setConfirmDelete(false)}>Keep</button>
            </span>
          )}
        </div>
      )}
      {log !== null && <pre className="log">{log || "No setup output yet."}</pre>}
      <ErrorBox message={error} />
    </article>
  );
}

function RegisterForm({ onCreated }: { onCreated: () => void }) {
  const [f, setF] = useState({ name: "", repo_url: "", default_branch: "main", app_path: "", test_command: "", sandbox_mode: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [urlError, setUrlError] = useState<string | null>(null);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!isRepoUrl(f.repo_url)) {
      setUrlError("Use an https:// URL, such as https://github.com/owner/name.");
      return;
    }
    setUrlError(null);
    setBusy(true); setError(null);
    try {
      await api("/projects", { body: f });
      setF({ name: "", repo_url: "", default_branch: "main", app_path: "", test_command: "", sandbox_mode: "" });
      onCreated();
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };

  return (
    <form className="panel panel-pad stack" onSubmit={submit}>
      <div>
        <h2>Add a repository</h2>
        <p className="small muted" style={{ marginTop: 4 }}>AgentOps clones it and installs its dependencies in its own environment. It needs a pytest suite.</p>
      </div>
      <div className="form-grid">
        <div className="field">
          <label htmlFor="p-name">Name</label>
          <input id="p-name" className="input" required pattern="[a-z0-9_\-]+" value={f.name} onChange={set("name")} placeholder="shop" />
          <span className="hint">Lowercase letters, digits, - and _. Used in API calls and webhook URLs.</span>
        </div>
        <div className="field">
          <label htmlFor="p-url">Git clone URL</label>
          <input id="p-url" className="input" required value={f.repo_url} onChange={(e) => { setUrlError(null); set("repo_url")(e); }} placeholder="https://github.com/you/shop.git" aria-invalid={urlError ? true : undefined} aria-describedby={urlError ? "p-url-error" : undefined} />
          {urlError && <span id="p-url-error" className="field-error" role="alert">{urlError}</span>}
        </div>
        <div className="field">
          <label htmlFor="p-branch">Branch</label>
          <input id="p-branch" className="input" value={f.default_branch} onChange={set("default_branch")} />
        </div>
        <div className="field">
          <label htmlFor="p-path">App folder (optional)</label>
          <input id="p-path" className="input" value={f.app_path} onChange={set("app_path")} placeholder="services/orders" />
        </div>
        <div className="field">
          <label htmlFor="p-test">Test command (optional)</label>
          <input id="p-test" className="input mono" value={f.test_command} onChange={set("test_command")} placeholder="python -m pytest -q" />
        </div>
        <div className="field">
          <label htmlFor="p-sb">Sandbox</label>
          <select id="p-sb" className="select" value={f.sandbox_mode} onChange={set("sandbox_mode")}>
            <option value="">Server default</option>
            <option value="subprocess">Subprocess</option>
            <option value="docker">Docker (no network)</option>
            <option value="auto">Auto</option>
          </select>
        </div>
      </div>
      <ErrorBox message={error} />
      <div><button className="btn primary" disabled={busy}>{busy ? "Adding" : "Add repository"}</button></div>
    </form>
  );
}

export default function ProjectsPage() {
  const { canAct } = useMe();
  const { data, error, reload } = useApi<Project[]>("/projects");
  const pending = (data ?? []).some((p) => p.setup_status === "pending" || p.setup_status === "running");
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(reload, 3000);
    return () => clearInterval(t);
  }, [pending]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Repositories</h1>
          <p>The code AgentOps investigates and fixes. Errors are matched to a repository by its name.</p>
        </div>
      </div>
      <div className="stack">
        <ErrorBox message={error} />
        {data && data.length > 0 && (
          <div className="proj-grid">{data.map((p) => <ProjectCard key={p.id} p={p} onChanged={reload} />)}</div>
        )}
        {data && data.length === 0 && (
          <div className="alert info">{canAct ? "No repositories yet. Add your first one below. It is saved on the backend and stays after a refresh." : "No repositories yet. This account can view them, and a member or admin can register one."}</div>
        )}
        {canAct && <RegisterForm onCreated={reload} />}
      </div>
    </>
  );
}
