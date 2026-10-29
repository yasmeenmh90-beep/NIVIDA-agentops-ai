import { writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, loadEnv, type Connect, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

function applySecurityHeaders(req: Connect.IncomingMessage, res: { setHeader: (name: string, value: string) => void }, withCsp: boolean) {
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("Referrer-Policy", "strict-origin-when-cross-origin");
  res.setHeader("X-Frame-Options", "DENY");
  res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  const host = String(req.headers.host ?? "");
  const forwarded = String(req.headers["x-forwarded-proto"] ?? "");
  const local = host.startsWith("localhost") || host.startsWith("127.0.0.1") || host.startsWith("[::1]");
  if (forwarded === "https" && !local) {
    res.setHeader("Strict-Transport-Security", "max-age=15552000; includeSubDomains");
  }
  if (withCsp) {
    res.setHeader(
      "Content-Security-Policy",
      [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "img-src 'self' data: https:",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "script-src 'self'",
        "connect-src 'self' http: https:",
      ].join("; "),
    );
  }
}

function securityHeaders(): Plugin {
  const attach = (server: { middlewares: { use: (fn: Connect.NextHandleFunction) => void } }, withCsp: boolean) => {
    server.middlewares.use((req, res, next) => {
      applySecurityHeaders(req, res, withCsp);
      next();
    });
  };
  return {
    name: "agentops-security-headers",
    configureServer(server) { attach(server, false); },
    configurePreviewServer(server) { attach(server, true); },
  };
}

function publicFiles(site: string): Plugin {
  const origin = site.trim().replace(/\/+$/, "");
  return {
    name: "agentops-public-files",
    transformIndexHtml(html) {
      const image = origin ? `${origin}/og.png` : "/og.png";
      const next = html.replaceAll("%OG_IMAGE%", image);
      if (!origin) return next.replace("<!--canonical-->", "");
      return next.replace("<!--canonical-->", `<link rel="canonical" href="${origin}/" />`);
    },
    closeBundle() {
      const dist = resolve("dist");
      const robots = [
        "User-agent: *",
        "Allow: /help",
        "Allow: /privacy",
        "Allow: /terms",
        "Allow: /login",
        "Disallow: /incidents",
        "Disallow: /projects",
        "Disallow: /activity",
        "Disallow: /metrics",
        "Disallow: /integrations",
        "Disallow: /send",
        "Disallow: /team",
        "Disallow: /settings",
        "Disallow: /auth",
        ...(origin ? ["", `Sitemap: ${origin}/sitemap.xml`] : []),
        "",
      ].join("\n");
      writeFileSync(resolve(dist, "robots.txt"), robots);
      if (!origin) return;
      const urls = ["/", "/help", "/privacy", "/terms", "/login"];
      const xml = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        ...urls.map((path) => `  <url><loc>${origin}${path}</loc></url>`),
        "</urlset>",
        "",
      ].join("\n");
      writeFileSync(resolve(dist, "sitemap.xml"), xml);
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react(), securityHeaders(), publicFiles(env.VITE_SITE_URL ?? "")],
    server: { port: 5173 },
  };
});
