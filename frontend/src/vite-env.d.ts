/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
  readonly VITE_AGENTOPS_KEY?: string;
  /** Public site origin, no trailing slash. Used for canonical and social URLs. */
  readonly VITE_SITE_URL?: string;
  /** Optional analytics endpoint. Nothing is sent until a visitor allows it. */
  readonly VITE_ANALYTICS_ENDPOINT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
