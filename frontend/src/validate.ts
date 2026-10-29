/** Accept only http(s) URLs. Localhost stays allowed for development. */
export function normalizeHttpUrl(value: string): string | null {
  const raw = value.trim();
  if (!raw) return null;
  let url: URL;
  try { url = new URL(raw); } catch { return null; }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (!url.hostname) return null;
  const path = url.pathname.replace(/\/+$/, "");
  return `${url.origin}${path === "/" ? "" : path}`;
}

/** https clone URLs. The backend rejects other schemes unless it is configured for a local path. */
export function isRepoUrl(value: string): boolean {
  const raw = value.trim();
  try {
    const url = new URL(raw);
    return url.protocol === "https:" && Boolean(url.hostname);
  } catch {
    return false;
  }
}
