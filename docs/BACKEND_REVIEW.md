# Backend review and update

This revision preserves the incident pipeline and the redesigned React frontend. It focuses on verification correctness, API robustness, authentication edge cases, and deployment persistence.

## Changes

| Area | Issue found | Updated behavior |
|---|---|---|
| Fix verification | A failing test absent from the patched run could be counted as fixed | A fixed test must explicitly pass after the patch; missing or skipped previously completed tests block verification |
| Baseline validity | A collection failure before the patch did not automatically invalidate verification | Both baseline and patched runs must complete without collection errors or unsuccessful runner exits |
| Log uploads | The entire file was read into memory, then silently truncated | Read up to 5 MB plus one byte; return HTTP 413 for oversized files and 422 for empty files; close the upload |
| Async ingestion | File and Sentry ingestion performed blocking processing on the event loop | Dispatch ingestion through a worker thread |
| Repository updates | Explicit JSON null values could reach non-null database columns | Return a validation error; omit unchanged fields or use empty strings for supported blank overrides |
| Sentry authentication | With authentication disabled locally, an unsigned webhook could enter through the fallback | Require an explicit credential for fallback, even in local open mode |
| Webhook JSON | Invalid JSON or non-object payloads produced unhandled errors | Return HTTP 400 for malformed JSON and non-object top-level payloads |
| Password hashes | Invalid stored hex values could raise an exception during login | Reject malformed hashes safely |
| GitHub identity | A public profile email could be used to link an existing account | Use only verified email data from GitHub's authenticated email endpoint; reject conflicting linked identities |
| Docker persistence | The default SQLite database lived outside the mounted volume | Store it at `/data/agentops.db` by default, alongside persistent workspace data |
| Dependencies | `python-dotenv` was directly imported but only transitively installed | Declare it explicitly in backend requirements |
| Configuration | Shared example configuration contained credential-like values | Blank example secrets; exclude runtime configuration, database files, and logs from the deliverable |

## Validation

- Full backend run: **69 passed in 79.58 seconds** in an isolated Python environment, with local proxy bypass. This run included the original pipeline tests and new backend regression checks.
- After adding one further OAuth test: **49 unit and regression tests passed**, including rejection of an unverified GitHub public email. The final tree contains **70 collected tests**. The full suite was not repeated solely for this additional test.
- Frontend: `npm run build` passed (TypeScript and Vite production build).
- The pipeline tests use a real local HTTP server, local git repository, and subprocess test execution. The LLM, GitHub, OAuth, and notification services are mocked.
- A real per-project virtualenv dependency install also passed.

Run from the backend directory:

```bash
python -m pytest -q
```

For a machine with HTTP proxy settings, ensure local connections bypass the proxy, for example:

```bash
NO_PROXY=localhost,127.0.0.1 python -m pytest -q
```

## Setup notes

1. Copy `backend/.env.example` to `backend/.env` and enter your own configuration. Previous shared keys and webhook secrets should be rotated.
2. Install backend dependencies in a virtualenv so subprocess verification can find pytest even when the sandbox replaces `HOME`.
3. Install frontend dependencies with `npm ci` and run `npm run dev`.
4. For a fresh demo, create the first account or use an API key. The first account becomes admin.
5. Docker uses `sqlite:////data/agentops.db` unless overridden. Existing deployments must copy or migrate any old database into the mounted data path; the change does not migrate an old database automatically.

## Scope and limits

This is a targeted code review and regression update, not a production certification. Live Nebius inference, GitHub PR creation, Sentry delivery, SMTP delivery, and Docker execution were not exercised against real external services in this review. Historical README demonstrations are labeled separately. Browser visual verification remains unavailable.

Subprocess mode runs repository code on the host and is suitable only for trusted repositories. Setup commands also execute repository installation code. Docker test isolation does not isolate the earlier host dependency-install step. Process-local OAuth state, login throttling, and job coordination mean this version should use one backend worker; a shared job/state service is needed before scaling across processes. TLS, deployment access controls, backups, and database migrations remain deployment responsibilities.
