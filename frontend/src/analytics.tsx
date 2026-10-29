import { useEffect, useState } from "react";

const CHOICE = "agentops.analytics";

/** Build-time endpoint. Empty means analytics are not configured and nothing is sent. */
export function analyticsConfigured(): boolean {
  return Boolean((import.meta.env.VITE_ANALYTICS_ENDPOINT ?? "").trim());
}

function choice(): "allow" | "deny" | null {
  try {
    const v = localStorage.getItem(CHOICE);
    return v === "allow" || v === "deny" ? v : null;
  } catch {
    return "deny";
  }
}

function routeTemplate(path: string): string {
  return path
    .replace(/\/incidents\/\d+/g, "/incidents/:id")
    .replace(/\/auth\/callback.*/g, "/auth/callback");
}

/** Page names only. Never call this with logs, diffs, tokens, or incident text. */
export function trackPage(path: string) {
  const endpoint = (import.meta.env.VITE_ANALYTICS_ENDPOINT ?? "").trim();
  if (!endpoint || choice() !== "allow") return;
  const body = JSON.stringify({ event: "page_view", path: routeTemplate(path) });
  try {
    if (navigator.sendBeacon) navigator.sendBeacon(endpoint, new Blob([body], { type: "application/json" }));
    else void fetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true });
  } catch { /* analytics must not affect the product */ }
}

export function AnalyticsConsent() {
  const [open, setOpen] = useState(false);
  useEffect(() => { setOpen(analyticsConfigured() && choice() === null); }, []);
  if (!open) return null;
  const save = (value: "allow" | "deny") => {
    try { localStorage.setItem(CHOICE, value); } catch { /* ignore */ }
    setOpen(false);
    if (value === "allow") trackPage(window.location.pathname);
  };
  return (
    <div className="consent" role="dialog" aria-labelledby="consent-title" aria-describedby="consent-copy">
      <div>
        <strong id="consent-title">Analytics</strong>
        <p id="consent-copy">This build can send the page you open, with incident numbers removed, to an analytics endpoint configured by the operator. Logs, code, and tokens are not included.</p>
      </div>
      <div className="row">
        <button type="button" className="btn sm" onClick={() => save("deny")}>Decline</button>
        <button type="button" className="btn sm primary" onClick={() => save("allow")}>Allow</button>
      </div>
    </div>
  );
}
