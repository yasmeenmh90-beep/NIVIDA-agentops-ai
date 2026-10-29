import { useEffect } from "react";
import { trackPage } from "./analytics";

function ensureMeta(attr: "name" | "property", key: string) {
  let el = document.head.querySelector(`meta[${attr}="${key}"]`);
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute(attr, key);
    document.head.appendChild(el);
  }
  return el;
}

export function siteOrigin(): string {
  return (import.meta.env.VITE_SITE_URL ?? "").trim().replace(/\/+$/, "");
}

export function usePageMeta(title: string, description: string, index: boolean) {
  useEffect(() => {
    document.title = title;
    document.documentElement.lang = "en";
    ensureMeta("name", "description").setAttribute("content", description);
    ensureMeta("name", "robots").setAttribute("content", index ? "index, follow" : "noindex, nofollow");
    ensureMeta("property", "og:title").setAttribute("content", title);
    ensureMeta("property", "og:description").setAttribute("content", description);
    ensureMeta("property", "og:type").setAttribute("content", "website");
    const origin = siteOrigin();
    const path = window.location.pathname;
    const canonical = document.head.querySelector('link[rel="canonical"]');
    if (origin) {
      const href = origin + path;
      ensureMeta("property", "og:url").setAttribute("content", href);
      ensureMeta("property", "og:image").setAttribute("content", `${origin}/og.png`);
      if (canonical) canonical.setAttribute("href", href);
      else {
        const link = document.createElement("link");
        link.rel = "canonical";
        link.href = href;
        document.head.appendChild(link);
      }
    }
    trackPage(path);
  }, [title, description, index]);
}

const APP_COPY: Record<string, [string, string]> = {
  "/": ["Incidents", "Review detected incidents, fixes waiting for approval, and recent agent activity."],
  "/projects": ["Repositories", "Register the Python repositories AgentOps investigates and tests."],
  "/activity": ["Activity", "Follow agent actions and decisions across incidents."],
  "/metrics": ["Metrics", "Resolution metrics computed from incidents stored on this backend."],
  "/integrations": ["Integrations", "Connect Sentry, GitHub webhooks, and notifications for this backend."],
  "/send": ["Send a test error", "Submit a Python traceback or log file to the incident pipeline."],
  "/team": ["Team", "Manage who can view the workspace and who can approve fixes."],
  "/settings": ["Settings", "Set the backend URL and review which integrations are configured."],
  "/help": ["Help", "How AgentOps investigates an incident and where a human approves the pull request."],
  "/privacy": ["Privacy policy", "What AgentOps AI stores in the browser and which services the backend can call."],
  "/terms": ["Terms", "Permitted use, human approval, and the limits of AI-generated fixes."],
};

export function appMeta(pathname: string, waiting: number): { title: string; description: string } {
  const match = pathname.startsWith("/incidents/")
    ? ["Incident", "Diagnosis, proposed diff, test results, and the approval decision for this incident."] as [string, string]
    : APP_COPY[pathname] ?? ["Page not found", "That page is not part of AgentOps AI."];
  const prefix = waiting > 0 ? `(${waiting}) ` : "";
  return {
    title: `${prefix}${match[0]} · AgentOps AI`,
    description: match[1],
  };
}
