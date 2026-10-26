import { useEffect, useState } from "react";
import { api, subscribe, type Incident, type IncidentDetail, type SystemStatus } from "./api/client";
import { IncidentView } from "./components/IncidentView";
import { STATUS_LABEL, Severity, ago, statusTone } from "./components/bits";

const BUGS = [
  { id: 1, label: "Checkout without a discount code" },
  { id: 2, label: "Fetch an order" },
  { id: 3, label: "Open the last page of orders" },
];

const BAR: Record<string, string> = {
  AWAITING_APPROVAL: "border-l-gate",
  PR_CREATED: "border-l-pass",
  FAILED: "border-l-fail",
  NEEDS_HUMAN: "border-l-fail",
  REJECTED: "border-l-line",
};

function Integrations({ s }: { s: SystemStatus | null }) {
  if (!s) return null;
  const item = (ok: boolean, label: string, title: string) => (
    <span title={title} className="inline-flex items-center gap-1.5">
      <span className={`h-2 w-2 rounded-full ${ok ? "bg-pass" : "bg-fail"}`} aria-hidden />
      {label}
      <span className="sr-only">{ok ? "configured" : "not configured"}</span>
    </span>
  );
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted">
      {item(s.llm.configured, "Nemotron", s.llm.reasoning_model)}
      {item(s.tavily.configured, "Tavily", "Web search for the Investigator")}
      {item(s.github.configured, "GitHub", s.github.repo || "not configured")}
      {item(true, `Sandbox: ${s.sandbox.mode}`, "Where tests run")}
    </div>
  );
}

export default function App() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [detail, setDetail] = useState<IncidentDetail | null>(null);
  const [sys, setSys] = useState<SystemStatus | null>(null);
  const [offline, setOffline] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    api.status().then(setSys).catch(() => setOffline(true));
    return subscribe<Incident[]>("/incidents/stream", (list) => {
      setOffline(false);
      setIncidents(list);
      setSelected((cur) => cur ?? list[0]?.id ?? null);
    }, () => setOffline(true));
  }, []);

  useEffect(() => {
    setDetail(null);
    if (selected == null) return;
    return subscribe<IncidentDetail>(`/incidents/${selected}/stream`, setDetail);
  }, [selected]);

  async function trigger(bug: number) {
    setNotice("");
    try {
      const r = await api.trigger(bug);
      setNotice(`Sent the request. The orders API answered ${r.status_codes.join(", ")}.`);
    } catch (e) {
      setNotice((e as Error).message);
    }
  }

  async function reset() {
    if (!confirm("Delete all incidents and reseed the demo orders? The demo code is not changed.")) return;
    await api.reset();
    setSelected(null);
    setNotice("");
  }

  return (
    <div className="grid min-h-full grid-rows-[auto_1fr] md:h-full">
      <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-line bg-panel px-5 py-3">
        <div className="mr-auto">
          <h1 className="text-lg font-bold leading-none">AgentOps AI</h1>
          <p className="text-sm text-muted">Finds the bug, fixes it, proves the fix, then asks you.</p>
        </div>
        <Integrations s={sys} />
      </header>

      <div className="grid min-h-0 grid-cols-1 md:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="flex min-h-0 flex-col border-b border-line bg-panel md:border-r md:border-b-0">
          <div className="border-b border-line p-4">
            <p className="font-bold">Break the demo orders API</p>
            <p className="mb-2 text-sm text-muted">Each request hits a planted bug and writes an error log.</p>
            <div className="flex flex-col gap-1.5">
              {BUGS.map((b) => (
                <button key={b.id} onClick={() => trigger(b.id)}
                  className="rounded border border-line px-3 py-1.5 text-left text-sm hover:border-run hover:text-run">
                  {b.label}
                </button>
              ))}
            </div>
            {notice && <p className="mt-2 text-sm text-muted" role="status">{notice}</p>}
          </div>

          <nav className="max-h-72 min-h-0 flex-1 overflow-y-auto md:max-h-none" aria-label="Incidents">
            {offline && (
              <p className="m-4 text-sm text-fail">Can't reach the backend. Start it with <code className="font-mono">uvicorn app.main:app</code>.</p>
            )}
            {!offline && incidents.length === 0 && (
              <p className="m-4 text-sm text-muted">No incidents. Break something above to watch the agents work.</p>
            )}
            <ul>
              {incidents.map((i) => (
                <li key={i.id}>
                  <button onClick={() => setSelected(i.id)} aria-current={selected === i.id}
                    className={`block w-full border-b border-l-4 border-b-line px-4 py-3 text-left ${BAR[i.status] ?? "border-l-run"} ${selected === i.id ? "bg-ground" : "hover:bg-[#f3f5f8]"}`}>
                    <span className="flex items-center gap-2 text-xs">
                      <span className={`font-bold ${statusTone(i.status)}`}>{STATUS_LABEL[i.status]}</span>
                      <Severity level={i.severity} />
                      <span className="ml-auto text-muted">{ago(i.last_seen)}</span>
                    </span>
                    <span className="mt-1 line-clamp-2 block text-sm font-bold leading-snug">{i.title}</span>
                    <span className="text-xs text-muted">#{i.id} · {i.occurrences}×</span>
                  </button>
                </li>
              ))}
            </ul>
          </nav>
          <button onClick={reset} className="border-t border-line px-4 py-2 text-left text-sm text-muted hover:text-fail">
            Reset demo
          </button>
        </aside>

        <main className="min-h-0 md:overflow-y-auto">
          {detail ? (
            <IncidentView d={detail} />
          ) : (
            <div className="p-8 text-muted">{selected ? "Loading incident…" : "Select an incident."}</div>
          )}
        </main>
      </div>
    </div>
  );
}
