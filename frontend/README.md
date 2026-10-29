# AgentOps AI — Dashboard

A React and TypeScript (Vite) dashboard for the AgentOps AI backend. It talks to the real API and has no mock data.

| Page | What you can do |
|---|---|
| **Incidents** | Watch incidents update live over SSE. A "Waiting for you" queue shows fixes ready for approval. Filter by status |
| **Incident detail** | Live pipeline rail; root cause with evidence; diff with the regression test highlighted; before/after test proof; agent console and timeline; PR history; Nemotron token use per agent; a "This error came back" comparison for regressions. Actions: **Approve and open PR**, **Reject**, **Ask for changes** (revises the PR), **Stop the agents**, **Run again** |
| **Repositories** | Register, edit (branch, folder, test/install command, sandbox, Docker image, protected paths), delete, sync and reinstall, setup log |
| **Activity** | Everything across all incidents, live, with optional agent tool calls |
| **Integrations** | Copy-ready Sentry and GitHub webhook URLs and steps per repository, handler snippets, a **Send a test alert** button, and desktop notifications with sound |
| **Sign in / Team** | Email and password or **Continue with GitHub**. The first account is the admin. New accounts are viewers until an admin makes them members on the Team page. Viewers see everything but can't act |
| **Metrics** | Verified-fix rate, first-attempt rate, approval rate, time to fix, PR and merge, status and severity split, token use |
| **Send a test error** | Paste a traceback or upload a log file to trigger the pipeline |
| **Settings** | Set the backend URL, API key and your approver name, then check which integrations are configured |

## Developer touches

- The connection state in the workspace bar comes from the live incident stream, with polling if the stream drops.
- A **live feed terminal** prints every real status change as it happens.
- An **agent console** replays each agent step and tool call like a log.
- The **pipeline rail** draws itself, and the active stage pulses while agents work.
- A **⌘K / Ctrl+K command palette** jumps to any page or incident.
- **Metrics** count up, and the verified-fix rate is an animated gauge.
- Animations are turned off when the OS asks for reduced motion.

## Run it

The backend must be running first (see the main README).

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

Then open **http://localhost:5173** and create the admin account (the first account is the admin). Teammates sign up on the same page.

`VITE_API_URL` in `.env` sets the backend URL (default `http://localhost:8000`); you can also change it under **Backend connection** on the sign-in page. Operators can connect with a service API key there instead of an account.

**CORS:** the backend allows `http://localhost:5173` by default. If you serve the dashboard from another URL, add it to `CORS_ORIGINS` in `backend/.env`.

## Build for production

```bash
npm run build
```

The build writes static files to `dist/`. Deploy them to any static host and add that host's URL to the backend's `CORS_ORIGINS`. Set `VITE_SITE_URL` to that public origin before building if you want canonical URLs and `sitemap.xml`. Leave `VITE_ANALYTICS_ENDPOINT` empty unless you have an endpoint that should receive page names only, and only after the visitor allows it.

Signed-out visitors get a short product page, plus Help, Privacy, and Terms. The dashboard, including incidents and approval, stays behind sign-in.

## Production notes

- Secrets stay in the backend environment. The dashboard can store a service API key in this browser's local storage; do not commit one.
- The dev server sends basic security headers. Preview and production builds add a content security policy. HSTS is set only when the request is already HTTPS and the host is not localhost.
- `robots.txt` allows the public pages and disallows the workspace routes. A sitemap is emitted only when `VITE_SITE_URL` is set.

## Structure

```
src/
├── api.ts                 types (mirror backend schemas), fetch client, live SSE hook with polling fallback
├── App.tsx                layout, navigation, integration status lights
├── styles.css             design tokens and all styles
├── components/ui.tsx      status/severity badges, pipeline rail, diff viewer, test proof
└── pages/                 Incidents, IncidentDetail, Projects, Metrics, SendError, Settings
```
