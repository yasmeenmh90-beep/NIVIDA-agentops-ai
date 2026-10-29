import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Incident, ServerStatus, STATUS_LABEL, api, getConfig } from "../api";

export function useReducedMotion() {
  const [r, setR] = useState(() => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
  useEffect(() => {
    const m = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!m) return;
    const h = () => setR(m.matches);
    m.addEventListener("change", h);
    return () => m.removeEventListener("change", h);
  }, []);
  return r;
}

// ---------------- numbers ----------------

/** Counts up to `value` when it first appears or changes. */
export function CountUp({ value, format = (n) => Math.round(n).toLocaleString(), duration = 900 }: { value: number; format?: (n: number) => string; duration?: number }) {
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(reduced ? value : 0);
  const from = useRef(0);
  useEffect(() => {
    if (reduced) { setShown(value); return; }
    const start = performance.now();
    const a = from.current;
    let raf = 0;
    const tick = (t: number) => {
      const k = Math.min(1, (t - start) / duration);
      const e = 1 - Math.pow(1 - k, 3);
      setShown(a + (value - a) * e);
      if (k < 1) raf = requestAnimationFrame(tick);
      else from.current = value;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, reduced, duration]);
  return <>{format(shown)}</>;
}

/** Ring gauge for a 0..1 rate. */
export function Gauge({ value, size = 168, label }: { value: number | null; size?: number; label: string }) {
  const r = (size - 18) / 2;
  const c = 2 * Math.PI * r;
  const v = value ?? 0;
  return (
    <div className="gauge" style={{ width: size, height: size }} role="img" aria-label={`${label}: ${value === null ? "no data" : Math.round(v * 100) + "%"}`}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <defs>
          <linearGradient id="gauge-grad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="var(--teal)" />
            <stop offset="100%" stopColor="var(--sky)" />
          </linearGradient>
        </defs>
        <circle cx={size / 2} cy={size / 2} r={r} className="gauge-track" />
        <circle
          cx={size / 2} cy={size / 2} r={r} className="gauge-fill"
          style={{ strokeDasharray: c, ["--gauge-off" as string]: `${c * (1 - v)}`, ["--gauge-c" as string]: `${c}` }}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <div className="gauge-center">
        <span className="gauge-value">{value === null ? "—" : <CountUp value={v * 100} format={(n) => `${Math.round(n)}%`} />}</span>
      </div>
    </div>
  );
}

// ---------------- terminal ----------------

export interface TermLine { id: string; kind: "cmd" | "out" | "ok" | "err" | "warn" | "dim" | "call" | "ret"; text: ReactNode; ts?: string }

/** Terminal that reveals lines one by one (new lines animate in; old ones show instantly after first reveal). */
export function Terminal({ title, lines, cursor = false, maxHeight = 460, reveal = true }: { title: ReactNode; lines: TermLine[]; cursor?: boolean; maxHeight?: number; reveal?: boolean }) {
  const reduced = useReducedMotion();
  const [count, setCount] = useState(reduced || !reveal ? lines.length : 0);
  const body = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    if (count >= lines.length) return;
    if (reduced || !reveal) { setCount(lines.length); return; }
    const t = setTimeout(() => setCount((c) => c + 1), count < 3 ? 120 : 45);
    return () => clearTimeout(t);
  }, [count, lines.length, reduced, reveal]);

  useEffect(() => {
    const el = body.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [count]);

  return (
    <div className="term">
      <div className="term-bar">
        <span className="term-dots" aria-hidden="true"><i /><i /><i /></span>
        <span className="term-title">{title}</span>
      </div>
      <div
        className="term-body" ref={body} style={{ maxHeight }} tabIndex={0}
        onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 30; }}
      >
        {lines.slice(0, count).map((l) => (
          <div key={l.id} className={`tl tl-${l.kind}`}>
            {l.ts !== undefined && <span className="tl-ts">{l.ts}</span>}
            <span className="tl-text">{l.text}</span>
          </div>
        ))}
        {cursor && count >= lines.length && <div className="tl tl-cmd"><span className="tl-text"><span className="caret" /></span></div>}
      </div>
    </div>
  );
}

export const clock = (iso?: string) => (iso ? new Date(iso).toLocaleTimeString(undefined, { hour12: false }) : new Date().toLocaleTimeString(undefined, { hour12: false }));

// ---------------- boot sequence ----------------

type Check = { label: string; state: "wait" | "ok" | "fail"; note?: string };

/** One-time startup screen that really checks the backend before showing the app. */
export function Boot({ onDone }: { onDone: () => void }) {
  const reduced = useReducedMotion();
  const { url } = getConfig();
  const [checks, setChecks] = useState<Check[]>([
    { label: `connect ${url.replace(/^https?:\/\//, "")}`, state: "wait" },
    { label: "authenticate api key", state: "wait" },
    { label: "nemotron on nebius token factory", state: "wait" },
    { label: "open live incident stream", state: "wait" },
  ]);
  const set = (i: number, c: Partial<Check>) => setChecks((xs) => xs.map((x, j) => (j === i ? { ...x, ...c } : x)));

  useEffect(() => {
    if (reduced) { onDone(); return; }
    let alive = true;
    const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
    (async () => {
      await wait(250);
      try {
        const h = await fetch(`${url}/health`);
        set(0, { state: h.ok ? "ok" : "fail", note: h.ok ? "200" : String(h.status) });
      } catch { set(0, { state: "fail", note: "unreachable" }); }
      await wait(180);
      let st: ServerStatus | null = null;
      try { st = await api<ServerStatus>("/status"); set(1, { state: "ok", note: st.auth_enabled ? "accepted" : "auth off" }); }
      catch (e) { set(1, { state: "fail", note: (e as Error).message.slice(0, 40) }); }
      await wait(180);
      if (st) set(2, { state: st.llm.configured ? "ok" : "fail", note: st.llm.configured ? st.llm.reasoning_model.replace("nvidia/", "") : "no key" });
      else set(2, { state: "fail", note: "skipped" });
      await wait(180);
      set(3, { state: st ? "ok" : "fail", note: st ? "sse" : "skipped" });
      await wait(650);
      if (alive) onDone();
    })();
    const max = setTimeout(() => alive && onDone(), 4500);
    return () => { alive = false; clearTimeout(max); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="boot" onClick={onDone} role="status" aria-live="polite">
      <div className="boot-box">
        <div className="boot-logo">
          <svg viewBox="0 0 120 40" className="boot-wave" aria-hidden="true">
            <path d="M2 26h22l9-18 13 30 10-22 7 10h55" />
          </svg>
          <span>agentops<span className="boot-ai">.ai</span></span>
        </div>
        <div className="boot-lines">
          {checks.map((c, i) => (
            <div key={i} className={`boot-line ${c.state}`} style={{ animationDelay: `${i * 90}ms` }}>
              <span className="boot-mark">{c.state === "wait" ? <span className="spinner" /> : c.state === "ok" ? "✓" : "✗"}</span>
              <span>{c.label}</span>
              <span className="boot-dots" />
              <span className="boot-note">{c.note ?? ""}</span>
            </div>
          ))}
        </div>
        <span className="boot-skip">click to skip</span>
      </div>
    </div>
  );
}

// ---------------- command palette ----------------

interface Cmd { id: string; label: string; hint: string; go: () => void }

export function CommandPalette({ open, onClose, incidents }: { open: boolean; onClose: () => void; incidents: Incident[] }) {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  const all: Cmd[] = useMemo(() => {
    const page = (path: string, label: string): Cmd => ({ id: path, label, hint: "page", go: () => nav(path) });
    return [
      page("/", "Incidents"),
      page("/projects", "Repositories"),
      page("/activity", "Activity"),
      page("/metrics", "Metrics"),
      page("/integrations", "Integrations and webhooks"),
      page("/send", "Send a test error"),
      page("/settings", "Settings"),
      ...incidents.filter((i) => i.status === "AWAITING_APPROVAL").map((i) => ({
        id: `a${i.id}`, label: `Review fix #${i.id}: ${i.title}`, hint: "needs approval", go: () => nav(`/incidents/${i.id}`),
      })),
      ...incidents.map((i) => ({ id: `i${i.id}`, label: `#${i.id} ${i.title}`, hint: STATUS_LABEL[i.status] ?? i.status, go: () => nav(`/incidents/${i.id}`) })),
    ];
  }, [incidents, nav]);

  const list = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return all.slice(0, 12);
    return all.filter((c) => c.label.toLowerCase().includes(t) || c.hint.toLowerCase().includes(t)).slice(0, 12);
  }, [q, all]);

  useEffect(() => { if (open) { setQ(""); setSel(0); setTimeout(() => input.current?.focus(), 10); } }, [open]);
  useEffect(() => setSel(0), [q]);

  if (!open) return null;
  const run = (c?: Cmd) => { if (c) { c.go(); onClose(); } };

  return (
    <div className="palette-wrap" onMouseDown={onClose}>
      <div className="palette" role="dialog" aria-label="Command palette" onMouseDown={(e) => e.stopPropagation()}>
        <div className="palette-input">
          <span className="palette-prompt">›</span>
          <input
            ref={input} autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Jump to a page or incident"
            aria-label="Search commands"
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(list.length - 1, s + 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
              else if (e.key === "Enter") run(list[sel]);
              else if (e.key === "Escape") onClose();
            }}
          />
          <kbd>esc</kbd>
        </div>
        <ul className="palette-list" role="listbox">
          {list.length === 0 && <li className="palette-empty">Nothing matches “{q}”</li>}
          {list.map((c, i) => (
            <li key={c.id} role="option" aria-selected={i === sel} className={i === sel ? "on" : ""} onMouseEnter={() => setSel(i)} onClick={() => run(c)}>
              <span className="palette-label">{c.label}</span>
              <span className="palette-hint">{c.hint}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
