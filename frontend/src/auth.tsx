import { FormEvent, createContext, useCallback, useContext, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AuthConfig, Me, api, getConfig, saveBackendUrl, saveConfig, setSession, useConfigVersion } from "./api";
import { BrandMark, ErrorBox } from "./components/ui";
import { normalizeHttpUrl } from "./validate";
import { usePageMeta } from "./seo";
import { DEMO_EMAIL, DEMO_PASSWORD } from "./demo";

// ---------------- state ----------------

type AuthState =
  | { phase: "loading" }
  | { phase: "signed-out"; config: AuthConfig | null; error?: string }
  | { phase: "ready"; me: Me; config: AuthConfig | null };

const MeCtx = createContext<{ me: Me; config: AuthConfig | null; signOut: () => void; refresh: () => void } | null>(null);

export function useMe() {
  const c = useContext(MeCtx)!;
  const canAct = c.me.role === "member" || c.me.role === "admin";
  return { ...c, canAct, isAdmin: c.me.role === "admin" };
}

export const MeProvider = MeCtx.Provider;

export function useAuth() {
  const version = useConfigVersion();
  const [state, setState] = useState<AuthState>({ phase: "loading" });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    (async () => {
      let config: AuthConfig | null = null;
      try { config = await api<AuthConfig>("/auth/config"); } catch (e) {
        if (alive) setState({ phase: "signed-out", config: null, error: (e as Error).message });
        return;
      }
      try {
        const me = await api<Me>("/auth/me");
        if (alive) setState({ phase: "ready", me, config });
      } catch {
        if (alive) setState({ phase: "signed-out", config });
      }
    })();
    return () => { alive = false; };
  }, [version, tick]);

  useEffect(() => {
    const h = () => setState((s) => (s.phase === "ready" && s.me.kind !== "open" ? { phase: "signed-out", config: s.config, error: "Your session has ended. Sign in again." } : s));
    window.addEventListener("agentops:unauthorized", h);
    return () => window.removeEventListener("agentops:unauthorized", h);
  }, []);

  useEffect(() => {
    const h = () => setState((s) => (s.phase === "ready" ? { phase: "signed-out", config: s.config } : s));
    window.addEventListener("agentops:signup", h);
    return () => window.removeEventListener("agentops:signup", h);
  }, []);

  const signOut = useCallback(async () => {
    try { await api("/auth/logout", { method: "POST" }); } catch { /* already gone */ }
    setSession("");
    saveConfig(getConfig().url, "");
    try { sessionStorage.removeItem("agentops.booted"); sessionStorage.removeItem("agentops.demo"); } catch { /* ignore */ }
  }, []);

  return { state, signOut, refresh: () => setTick((t) => t + 1) };
}

// ---------------- GitHub callback ----------------

export function AuthCallback() {
  const nav = useNavigate();
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const p = new URLSearchParams(window.location.hash.slice(1));
    const token = p.get("token");
    const err = p.get("error");
    history.replaceState(null, "", window.location.pathname);
    if (token) { setSession(token); nav("/", { replace: true }); }
    else setError(err || "GitHub sign-in did not complete.");
  }, [nav]);
  return (
    <div className="auth-wrap">
      <div className="auth-card stack" style={{ maxWidth: 420 }}>
        {error ? (
          <>
            <h2>Couldn't sign you in</h2>
            <ErrorBox message={error} />
            <button className="btn primary" onClick={() => nav("/", { replace: true })}>Back to sign in</button>
          </>
        ) : <p className="row"><span className="spinner" /> Signing you in</p>}
      </div>
    </div>
  );
}

// ---------------- sign-in page ----------------

const DEMO_LINES = [
  ["$", "agentops watch shop"],
  ["!", "KeyError: 'customer_id' in serialize_order()"],
  ["→", "investigator  read_file orders/serializers.py"],
  ["→", "fixer         submit_patch + regression test"],
  ["✓", "tester        fails before, passes after"],
  ["?", "waiting for your approval"],
];

function GithubIcon() {
  return (
    <svg viewBox="0 0 16 16" width="18" height="18" aria-hidden="true" fill="currentColor">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

export function LoginPage({ config, initialError }: { config: AuthConfig | null; initialError?: string }) {
  const firstRun = config ? !config.has_users : false;
  const [mode, setMode] = useState<"signin" | "signup">(firstRun ? "signup" : "signin");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(initialError ?? null);
  const [advanced, setAdvanced] = useState(false);
  const [url, setUrl] = useState(getConfig().url);
  const [key, setKey] = useState("");
  const [urlError, setUrlError] = useState<string | null>(null);
  const [demoEmail, setDemoEmail] = useState("");
  const [demoPassword, setDemoPassword] = useState("");
  const [demoError, setDemoError] = useState<string | null>(null);
  const [demoBusy, setDemoBusy] = useState(false);

  useEffect(() => { if (config && !config.has_users) setMode("signup"); }, [config]);
  useEffect(() => { if (config) setError((prev) => (prev === initialError ? null : prev)); }, [config, initialError]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const body = mode === "signup" ? { email, name, password } : { email, password };
      const r = await api<{ token: string }>(mode === "signup" ? "/auth/signup" : "/auth/login", { body });
      setSession(r.token);
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };

  const useKey = (e: FormEvent) => {
    e.preventDefault();
    const parsed = normalizeHttpUrl(url);
    if (!parsed) {
      setUrlError("Enter a backend URL that starts with http:// or https://.");
      return;
    }
    setUrlError(null);
    setUrl(parsed);
    saveBackendUrl(parsed);
    if (key.trim()) saveConfig(parsed, key);
  };

  const submitDemo = async (e: FormEvent) => {
    e.preventDefault();
    if (demoBusy) return;
    const mail = demoEmail.trim().toLowerCase();
    if (!mail || !demoPassword) {
      setDemoError("Enter the demo email and password.");
      return;
    }
    setDemoBusy(true);
    setDemoError(null);
    try {
      const res = await api<{ token: string }>("/auth/login", { method: "POST", body: { email: mail, password: demoPassword } });
      setDemoPassword("");
      setSession(res.token);
    } catch (err) {
      setDemoError((err as Error).message);
    } finally {
      setDemoBusy(false);
    }
  };

  const signupClosed = config?.signup === "closed" && config.has_users;
  usePageMeta(
    "Sign in · AgentOps AI",
    "Sign in to a viewer account on this backend, or to an operator account.",
    false,
  );

  return (
    <div className="auth-wrap">
      <div className="auth-grid">
        <section className="auth-pitch" aria-hidden="true">
          <div className="auth-brand"><BrandMark /><span>AgentOps<span className="brand-ai">.ai</span></span></div>
          <h1 className="auth-title">The on-call engineer that writes the fix and proves it.</h1>
          <p className="auth-sub">Errors come in. Nemotron finds the root cause, writes a fix and a regression test, and runs your tests. You approve, it opens the PR.</p>
          <div className="auth-term">
            {DEMO_LINES.map(([m, t], i) => (
              <div key={i} className={`auth-line m${i}`} style={{ animationDelay: `${0.35 + i * 0.45}s` }}>
                <span className="auth-m">{m}</span><span>{t}</span>
              </div>
            ))}
          </div>
          <p className="auth-foot">NVIDIA Nemotron on Nebius Token Factory</p>
        </section>

        <section className="auth-card stack">
          <Link to="/" className="small">← Product overview</Link>
          <form className="stack" onSubmit={submitDemo} style={{ gap: 14 }}>
            <div>
              <p className="eyebrow">Demo access</p>
              <h2>Open the workspace</h2>
              <p className="small muted" style={{ marginTop: 4 }}>Signs in as the viewer account on this server. It can read incidents and repositories. It cannot approve a fix, register a repository, or manage users.</p>
            </div>
            <p className="small muted">Email <span className="mono">{DEMO_EMAIL}</span>. Password <span className="mono">{DEMO_PASSWORD}</span>.</p>
            <div className="field">
              <label htmlFor="demo-email">Email</label>
              <input id="demo-email" className="input" type="email" value={demoEmail} onChange={(e) => { setDemoEmail(e.target.value); setDemoError(null); }} autoComplete="off" required />
            </div>
            <div className="field">
              <label htmlFor="demo-pass">Password</label>
              <input id="demo-pass" className="input" type="password" value={demoPassword} onChange={(e) => { setDemoPassword(e.target.value); setDemoError(null); }} autoComplete="off" required />
            </div>
            <ErrorBox message={demoError} />
            <div className="row">
              <button className="btn primary" type="submit" disabled={demoBusy}>{demoBusy ? "Opening" : "Sign in"}</button>
              <button className="btn" type="button" onClick={() => { setDemoEmail(DEMO_EMAIL); setDemoPassword(DEMO_PASSWORD); setDemoError(null); }}>Fill demo access</button>
            </div>
          </form>

          <details className="fold">
            <summary>Operator account</summary>
            <div className="stack" style={{ marginTop: 14 }}>
          <div>
            <h2>{firstRun ? "Create the admin account" : mode === "signup" ? "Create your account" : "Sign in with an account"}</h2>
            <p className="small muted" style={{ marginTop: 4 }}>
              {firstRun ? "The first account on this backend becomes the admin." : mode === "signup" ? "New accounts can view everything. An admin can let you approve fixes." : "For a backend account that already exists. Nebius, Tavily, and GitHub keys stay on the server."}
            </p>
          </div>

          {config?.github && (
            <>
              <a className="btn github" href={`${getConfig().url}/auth/github/start`}><GithubIcon /> Continue with GitHub</a>
              <div className="or"><span>or with email</span></div>
            </>
          )}

          <form className="stack" onSubmit={submit} style={{ gap: 14 }}>
            {mode === "signup" && (
              <div className="field">
                <label htmlFor="a-name">Name</label>
                <input id="a-name" className="input" value={name} onChange={(e) => setName(e.target.value)} required autoComplete="name" placeholder="yasmeen" />
                <span className="hint">Shown on pull requests you approve.</span>
              </div>
            )}
            <div className="field">
              <label htmlFor="a-email">Email</label>
              <input id="a-email" className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
            </div>
            <div className="field">
              <label htmlFor="a-pass">Password</label>
              <input id="a-pass" className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={mode === "signup" ? 8 : 1} autoComplete={mode === "signup" ? "new-password" : "current-password"} />
              {mode === "signup" && <span className="hint">At least 8 characters.</span>}
            </div>
            <ErrorBox message={error} />
            <button className="btn primary" disabled={busy || !config}>
              {busy ? <><span className="spinner" /> {mode === "signup" ? "Creating account" : "Signing in"}</> : mode === "signup" ? "Create account" : "Sign in"}
            </button>
          </form>

          {!firstRun && !signupClosed && (
            <p className="small muted" style={{ textAlign: "center" }}>
              {mode === "signin" ? "New here? " : "Already have an account? "}
              <button className="link" type="button" onClick={() => { setMode(mode === "signin" ? "signup" : "signin"); setError(null); }}>
                {mode === "signin" ? "Create an account" : "Sign in"}
              </button>
            </p>
          )}
          {signupClosed && mode === "signin" && <p className="small faint" style={{ textAlign: "center" }}>Sign-up is closed. Ask an admin for an account.</p>}

          <details className="fold" open={advanced} onToggle={(e) => setAdvanced((e.target as HTMLDetailsElement).open)}>
            <summary>Backend connection</summary>
            <form className="stack" onSubmit={useKey} style={{ gap: 12, marginTop: 12 }}>
              <div className="field">
                <label htmlFor="a-url">Backend URL</label>
                <input id="a-url" className="input mono" value={url} onChange={(e) => { setUrl(e.target.value); setUrlError(null); }} aria-invalid={urlError ? true : undefined} aria-describedby={urlError ? "a-url-error" : undefined} />
                {urlError && <span id="a-url-error" className="field-error" role="alert">{urlError}</span>}
              </div>
              <div className="field">
                <label htmlFor="a-key">Service API key (optional)</label>
                <input id="a-key" className="input mono" type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" />
                <span className="hint">For operators. Signs in with full access, without an account.</span>
              </div>
              <div><button className="btn sm">Connect</button></div>
            </form>
          </details>
          </div>
          </details>
        </section>
      </div>
    </div>
  );
}
