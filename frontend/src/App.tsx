import { createContext, useContext, useEffect, useRef, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { Incident, STATUS_LABEL, ServerStatus, getConfig, useApi, useLive } from "./api";
import { BrandMark } from "./components/ui";
import { Boot, CommandPalette, TermLine, clock } from "./components/anim";
import { alertUser } from "./alerts";
import ActivityPage from "./pages/Activity";
import TeamPage from "./pages/Team";
import { AuthCallback, LoginPage, MeProvider, useAuth, useMe } from "./auth";
import IntegrationsPage from "./pages/Integrations";
import IncidentsPage from "./pages/Incidents";
import IncidentPage from "./pages/IncidentDetail";
import ProjectsPage from "./pages/Projects";
import MetricsPage from "./pages/Metrics";
import SendErrorPage from "./pages/SendError";
import SettingsPage from "./pages/Settings";

type LiveIncidents = ReturnType<typeof useLive<Incident[]>> & { feed: TermLine[]; changed: Record<number, number> };
const IncidentsCtx = createContext<LiveIncidents | null>(null);
export const useIncidents = () => useContext(IncidentsCtx)!;

/** Turn successive incident snapshots into a live event feed (real changes only). */
function useFeed(data: Incident[] | null) {
  const prev = useRef<Map<number, Incident> | null>(null);
  const [feed, setFeed] = useState<TermLine[]>([]);
  const [changed, setChanged] = useState<Record<number, number>>({});
  useEffect(() => {
    if (!data) return;
    const now = new Map(data.map((i) => [i.id, i]));
    const lines: TermLine[] = [];
    const bumped: Record<number, number> = {};
    const stamp = Date.now();
    if (prev.current === null) {
      lines.push({ id: `s${stamp}`, kind: "cmd", text: `agentops watch --incidents   # ${data.length} known`, ts: clock() });
      [...data].slice(0, 6).reverse().forEach((i) =>
        lines.push({ id: `s${stamp}-${i.id}`, kind: "dim", text: `#${i.id} ${STATUS_LABEL[i.status]}  ${i.title.slice(0, 70)}`, ts: clock(i.updated_at) }));
    } else {
      for (const i of [...data].reverse()) {
        const p = prev.current.get(i.id);
        const ts = clock(i.updated_at);
        if (!p) {
          lines.push({ id: `${stamp}-n${i.id}`, kind: "warn", text: `#${i.id} new ${i.severity} incident  ${i.title.slice(0, 70)}`, ts });
          bumped[i.id] = stamp;
          alertUser(`New ${i.severity} incident #${i.id}`, i.title, `/incidents/${i.id}`, "problem");
        } else if (p.status !== i.status) {
          if (i.status === "AWAITING_APPROVAL") alertUser(`Fix ready for review: #${i.id}`, i.status_reason || i.title, `/incidents/${i.id}`, "attention");
          else if (i.status === "NEEDS_HUMAN" || i.status === "FAILED") alertUser(`#${i.id} ${STATUS_LABEL[i.status]}`, i.status_reason || i.title, `/incidents/${i.id}`, "problem");
          else if (i.status === "RESOLVED") alertUser(`#${i.id} fix merged`, i.title, `/incidents/${i.id}`, "good");
          const kind = i.status === "AWAITING_APPROVAL" ? "ok" : ["FAILED", "NEEDS_HUMAN"].includes(i.status) ? "err" : i.status === "RESOLVED" || i.status === "PR_CREATED" ? "ok" : "out";
          lines.push({ id: `${stamp}-s${i.id}`, kind, text: `#${i.id} ${STATUS_LABEL[p.status]} → ${STATUS_LABEL[i.status]}${i.pr_number && i.status === "PR_CREATED" ? `  PR #${i.pr_number}` : ""}`, ts });
          bumped[i.id] = stamp;
        } else if (p.occurrences !== i.occurrences) {
          lines.push({ id: `${stamp}-o${i.id}`, kind: "dim", text: `#${i.id} seen again (${i.occurrences}×), grouped`, ts });
          bumped[i.id] = stamp;
        }
      }
    }
    prev.current = now;
    if (lines.length) setFeed((f) => [...f, ...lines].slice(-80));
    if (Object.keys(bumped).length) setChanged((c) => ({ ...c, ...bumped }));
  }, [data]);
  return { feed, changed };
}

function NavIcon({ index }: { index: number }) {
  const paths = ["M12 3 2 21h20L12 3Zm0 6v5m0 3v1", "M4 4h6l2 3h8v13H4V4Z", "M3 12h4l3-8 4 16 3-8h4", "M4 20V10m8 10V4m8 16v-7", "M8 3v5m8-5v5M5 8h14v5a7 7 0 0 1-14 0V8Zm7 12v3", "m13 2-9 12h7l-1 8 10-13h-7l1-7Z", "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m18 0v-2a4 4 0 0 0-3-4M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm8-8a4 4 0 0 1 0 8", "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm-3-5h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3Z"];
  return <svg className="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[index]} /></svg>;
}

function Nav({ waiting, onPalette }: { waiting: number; onPalette: () => void }) {
  return (
    <nav className="nav" aria-label="Main">
      <NavLink to="/" end>
        <span className="nav-label"><NavIcon index={0} />Overview</span>
        {waiting > 0 && <span className="count" title={`${waiting} waiting for approval`}>{waiting}</span>}
      </NavLink>
      <NavLink to="/projects"><span className="nav-label"><NavIcon index={1} />Repositories</span></NavLink>
      <NavLink to="/activity"><span className="nav-label"><NavIcon index={2} />Activity</span></NavLink>
      <NavLink to="/metrics"><span className="nav-label"><NavIcon index={3} />Metrics</span></NavLink>
      <NavLink to="/integrations"><span className="nav-label"><NavIcon index={4} />Integrations</span></NavLink>
      <NavLink to="/send"><span className="nav-label"><NavIcon index={5} />Send a test error</span></NavLink>
      <NavLink to="/team"><span className="nav-label"><NavIcon index={6} />Team</span></NavLink>
      <NavLink to="/settings"><span className="nav-label"><NavIcon index={7} />Settings</span></NavLink>
      <button className="nav-k" onClick={onPalette} type="button">
        <span>Search</span><kbd>⌘K</kbd>
      </button>
    </nav>
  );
}

function LinkHelp() {
  return <div className="side-guide"><span className="guide-icon" aria-hidden="true">⌘</span><strong>Built for your workflow.</strong><p>Connect a repository. Send an error. Review the fix.</p><NavLink to="/integrations">Set up integrations <span aria-hidden="true">↗</span></NavLink></div>;
}

function Lights({ status }: { status: ServerStatus | null }) {
  if (!status) return <span>Not connected. Open Settings.</span>;
  const items: [string, boolean][] = [
    ["Nemotron", status.llm.configured],
    ["Tavily", status.tavily.configured],
    ["GitHub", status.webhooks.github],
    ["Sentry", status.webhooks.sentry],
    ["Email", status.notifications.email],
    ["Slack", status.notifications.slack],
  ];
  return (
    <>
      <div className="lights">
        {items.map(([name, on]) => (
          <span key={name} className={`light ${on ? "on" : ""}`} title={on ? `${name} configured` : `${name} not configured`}>
            <i />{name}
          </span>
        ))}
      </div>
      <div className="model-line" title="Reasoning model">{status.llm.reasoning_model}</div>
    </>
  );
}

function bootSeen() {
  try { return sessionStorage.getItem("agentops.booted") === "1"; } catch { return true; }
}

function UserChip() {
  const { me, signOut } = useMe();
  const u = me.user;
  return (
    <div className="user-chip">
      {u?.avatar_url ? <img className="avatar" src={u.avatar_url} alt="" /> : <span className="avatar">{me.name.slice(0, 1).toUpperCase()}</span>}
      <span className="user-meta">
        <span className="user-name">{me.kind === "user" ? me.name : me.kind === "key" ? "Service API key" : "Local mode"}</span>
        <span className={`role role-${me.role}`}>{me.role}</span>
      </span>
      {me.kind !== "open" && <button className="btn sm ghost" onClick={signOut} title="Sign out">Sign out</button>}
    </div>
  );
}

export default function App() {
  const loc = useLocation();
  const { state, signOut, refresh } = useAuth();
  if (loc.pathname === "/auth/callback") return <AuthCallback />;
  if (state.phase === "loading") return <div className="auth-wrap"><span className="spinner" style={{ color: "var(--amber)" }} /></div>;
  if (state.phase === "signed-out") return <LoginPage config={state.config} initialError={state.error} />;
  return (
    <MeProvider value={{ me: state.me, config: state.config, signOut, refresh }}>
      <Shell />
    </MeProvider>
  );
}

function Shell() {
  const { me } = useMe();
  const live = useLive<Incident[]>("/incidents/stream", "/incidents");
  const { feed, changed } = useFeed(live.data);
  const { data: status } = useApi<ServerStatus>("/status");
  const waiting = (live.data ?? []).filter((i) => i.status === "AWAITING_APPROVAL").length;
  const [booting, setBooting] = useState(() => !bootSeen());
  const [palette, setPalette] = useState(false);
  const loc = useLocation();

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((p) => !p); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  useEffect(() => { document.title = waiting ? `(${waiting}) AgentOps AI` : "AgentOps AI"; }, [waiting]);

  const finishBoot = () => {
    try { sessionStorage.setItem("agentops.booted", "1"); } catch { /* ignore */ }
    setBooting(false);
  };

  return (
    <IncidentsCtx.Provider value={{ ...live, feed, changed }}>
      <div className="bg-grid" aria-hidden="true" />
      {booting && <Boot onDone={finishBoot} />}
      <CommandPalette open={palette} onClose={() => setPalette(false)} incidents={live.data ?? []} />
      <a className="skip-link" href="#main-content">Skip to content</a>
      <div className={`shell ${booting ? "is-booting" : "is-ready"}`}>
        <aside className="side">
          <NavLink to="/" className="brand">
            <BrandMark />
            <span>
              <span className="brand-name">AgentOps<span className="brand-ai">.ai</span></span>
              <span className="brand-sub">ENGINEERING WORKSPACE</span>
            </span>
          </NavLink>
          <div className="workspace-switch"><span className="workspace-avatar">E</span><span>Engineering<small>Personal workspace</small></span><span aria-hidden="true">⌄</span></div>
          <div className="nav-section-label">PLATFORM</div>
          <Nav waiting={waiting} onPalette={() => setPalette(true)} />
          <LinkHelp />
          <div className="side-foot">
            <UserChip />
            <Lights status={status} />
            <span className="host" title="Backend URL"><i className={`host-dot ${live.mode}`} />{getConfig().url.replace(/^https?:\/\//, "")}</span>
          </div>
        </aside>
        <header className="topbar">
          <BrandMark />
          <Nav waiting={waiting} onPalette={() => setPalette(true)} />
        </header>
        <main className="main" id="main-content" key={loc.pathname}>
          <div className="workspace-bar"><span>Workspace <span className="workspace-slash">/</span> <strong>Engineering</strong></span><button className="workspace-search" onClick={() => setPalette(true)}>Search workspace <kbd>⌘ K</kbd></button><span className={`connection-pill ${live.mode}`}><i />{live.mode === "live" ? "Live connection" : live.mode === "polling" ? "Auto-refreshing" : "Connecting"}</span></div>
          {me.kind === "open" && (
            <div className="alert info" style={{ marginBottom: 20 }}>
              Anyone who can reach this backend can use it. <a href="#" onClick={(e) => { e.preventDefault(); window.dispatchEvent(new Event("agentops:signup")); }}>Create the admin account</a> to turn on sign-in.
            </div>
          )}
          {me.role === "viewer" && <div className="viewer-bar">View-only access. You can see everything; an admin can let you approve fixes.</div>}
          <Routes>
            <Route path="/" element={<IncidentsPage />} />
            <Route path="/incidents/:id" element={<IncidentPage />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/metrics" element={<MetricsPage />} />
            <Route path="/activity" element={<ActivityPage />} />
            <Route path="/integrations" element={<IntegrationsPage />} />
            <Route path="/send" element={<SendErrorPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/team" element={<TeamPage />} />
            <Route path="*" element={<div className="empty"><h3>Page not found</h3><NavLink className="btn" to="/">Go to incidents</NavLink></div>} />
          </Routes>
        </main>
      </div>
    </IncidentsCtx.Provider>
  );
}
