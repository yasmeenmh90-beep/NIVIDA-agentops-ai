# AgentOps AI

An autonomous incident engineer for Python services. Your app ships its exceptions to AgentOps; AgentOps finds the root cause in your repository, writes a minimal patch, proves it with **your own test suite** in a sandbox, and then **waits for a human to approve** before opening a GitHub pull request. It never merges and never pushes to your branches.

All reasoning runs on **NVIDIA Nemotron via Nebius Token Factory**; the Investigator can use **Tavily** for web evidence. Built for the Nebius × NVIDIA Global AI Hackathon 2026 (track: Coding and Agentic Engineering).

```
your service ─(handler / Sentry / logs)─► Detector ─► Investigator ─► Fixer ─────────► Tester ─► Approval gate ─► GitHub PR
                                          rules,      Nemotron +      Nemotron:        your pytest  a human        │
                                          memory,     repo tools,     fix + regression in sandbox                  │
                                          regression  Tavily, past    test    ▲                                    │
                                          detection   fixes                   └─ verification fails: retry (max 3) │
                                                                                                                   ▼
                    Slack / webhook / email alerts ◄── every key step        review "changes requested" ─► Fixer revises PR
                                                                             PR merged ─► RESOLVED (feeds memory)
```

States: `DETECTED → INVESTIGATING → FIXING → TESTING → AWAITING_APPROVAL → PR_CREATED → RESOLVED`, with `REVISING` (reviewer feedback being addressed), `REJECTED`, `PR_CLOSED`, `NEEDS_HUMAN` (no patch passed after 3 attempts; nothing is proposed) and `FAILED` (pipeline error; can be re-run).

## How it works

1. **Register a repository** (`POST /projects`). AgentOps clones it into its own workspace and installs its dependencies into a per-project virtualenv (`requirements*.txt` or `pyproject.toml`, or your own `install_command`).
2. **Ship errors** from your service with the bundled handler (`clients/python/agentops_handler.py`), or POST log lines / structured events.
3. **Detector** parses Python tracebacks (JSON log lines or plain text), maps frames to files in your repo, groups repeats by `exception + file:function`, and sets severity (write endpoints are high; escalates with occurrences).
4. Before investigating, AgentOps **hard-resets its clone to the latest default branch** and records the commit sha.
5. **Investigator** (Nemotron, native tool calling): `list_files`, `read_file`, `search_code`, `git_log`, `web_search` → `submit_root_cause` with file, line, explanation, evidence, confidence.
   It also gets the **most similar incidents already fixed in this project** (root cause + diff) as context. If the exact error was fixed and merged before, the incident is flagged as a **regression** and its severity raised.
6. **Fixer** (Nemotron) returns exact search/replace edits for the fix **plus a new regression test** that reproduces the bug. AgentOps validates them (unique match, `.py` only, no protected paths, no edits to existing tests, ≤ 40 lines of app code, ≤ 120 lines of test) and builds the unified diff itself, so the diff you review is exactly what gets applied. Invalid edits go back to the model to correct.
7. **Tester** runs your suite on a throwaway copy twice: original code + the new regression test, then the patched code. **Verified** = the regression test **fails before and passes after**, and no test that passed before fails now. A test that already passes on the buggy code is rejected as "does not reproduce the bug". This works on real repos where no failing test exists yet.
8. **Approval gate** (a human, not an LLM): Approve opens a PR on a new branch created from the analysed commit, with diagnosis, diff, regression test, test results and approver in the body. Reject closes the incident with a reason.
9. **Review loop** (GitHub webhook): when a collaborator submits a "Request changes" review or comments `/agentops <instructions>` on the PR, the Fixer revises the PR on top of its current contents, the Tester re-verifies against the original commit, and AgentOps pushes a commit to the PR branch and replies with the diff and results. Only `OWNER`/`MEMBER`/`COLLABORATOR` can trigger this; max 3 revisions per PR.
10. **Merge** → `RESOLVED` (closed unmerged → `PR_CLOSED`). Resolved incidents become memory for future investigations.

Notifications go to Slack, a generic JSON webhook and/or email at: new incident, verified fix ready, needs a human, pipeline error, PR opened, PR revised, merged.

### Safety
- No auto-merge; no pushes except a new `agentops/…` branch after a named human approves. Double approvals are blocked.
- The model can only read files and propose edits. No shell access for the model.
- File access is confined to the project's app directory (traversal and absolute paths rejected).
- Protected paths (CI, Dockerfile, dependency files, `conftest.py`, migrations, config) cannot be patched; existing tests cannot be edited. Patches are capped at 40 changed lines.
- Tests run on a copy: Docker with `--network none` and CPU/memory/pid limits, or a subprocess with a scrubbed environment (no API keys or tokens) and a timeout.
- Logs, file contents and web results are wrapped in `<untrusted_*>` tags and treated as data in prompts.
- API-key auth on every endpoint except `/health`. GitHub tokens are read from env vars, passed to git per command (never stored in remotes) and masked in error messages.
- Per-incident token budget, tool-round cap, and an audit log of every tool call, status change and decision.
- Incidents interrupted by a restart are marked `FAILED` on startup so they can be re-run.
- Webhooks are verified with HMAC-SHA256 (`GITHUB_WEBHOOK_SECRET`, `SENTRY_CLIENT_SECRET`); review feedback is treated as untrusted input in prompts.
- While a PR is open, repeats of the same error are grouped into its incident instead of starting new investigations.

**Frontend developers:** start with [docs/API.md](docs/API.md).

## Setup

Requirements: Python 3.11+, git. Docker optional.

```bash
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # set NEBIUS_API_KEY, API_KEYS, GITHUB_TOKEN (+ TAVILY_API_KEY)
python scripts/check_nebius.py                         # lists Nemotron models, tests tool calling
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 1. Register your repository

```bash
curl -X POST localhost:8000/projects -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d '{
  "name": "orders-api",
  "repo_url": "https://github.com/you/shop.git",
  "default_branch": "main",
  "app_path": "services/orders",
  "test_command": "python -m pytest -q"
}'
curl localhost:8000/projects/orders-api -H "X-API-Key: $KEY"    # wait for setup_status = "ready"
```

| Field | Default | Meaning |
|---|---|---|
| `repo_url` | (required) | `https://` clone URL. Private repos use the token in `github_token_env`. |
| `app_path` | `""` | Subdirectory containing the Python app (monorepos). Tests run here. |
| `github_repo` | derived from a github.com URL | `owner/name` for PRs; `""` disables PRs. |
| `github_token_env` | `GITHUB_TOKEN` | Env var holding the token for this project. |
| `test_command` | `python -m pytest -q -p no:cacheprovider` | Must be pytest (results are read from JUnit XML). |
| `install_command` | auto | Runs inside the project venv; auto installs `requirements*.txt` or `pip install -e .`, plus pytest. |
| `python_env` | `venv` | `venv` (isolated) or `system` (server interpreter). |
| `sandbox_mode` / `sandbox_image` | global | `docker` needs an image with your deps baked in (see `sandbox/Dockerfile`). |
| `protected_paths` | — | Extra comma-separated paths the Fixer may not touch. |

`PATCH /projects/{name}` changes settings (re-runs setup when needed); `POST /projects/{name}/setup` reinstalls after dependency changes.

### 2. Ship errors from your service

Copy `clients/python/agentops_handler.py` (standard library only) into your service:

```python
import logging, agentops_handler
agentops_handler.install(url="https://agentops.internal", api_key="...", project="orders-api", service="orders-api")

# FastAPI example: report unhandled errors with the route
@app.exception_handler(Exception)
async def on_error(request, exc):
    logging.getLogger("app").error("unhandled", exc_info=exc,
        extra={"http_method": request.method, "http_path": request.url.path})
    return JSONResponse({"detail": "Internal Server Error"}, status_code=500)
```

It captures `logger.exception(...)` / `exc_info=True` records plus uncaught exceptions (main and other threads), de-duplicates bursts, and posts in batches from a background thread so it never blocks your app.

Or send directly:

```bash
# JSON lines with a traceback (keys: traceback, exc_info, stack_trace, stacktrace, exception) or plain-text tracebacks
curl -X POST localhost:8000/ingest/logs -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"project": "orders-api", "source": "prod-1", "lines": ["...log lines..."]}'
curl -X POST localhost:8000/ingest/file -H "X-API-Key: $KEY" -F project=orders-api -F file=@app.log
```

### Optional: Sentry
Create a Sentry internal integration, enable the webhook (alert rule action or `error.created`), set its URL to `https://<agentops-host>/webhooks/sentry/<project-name>` and copy its client secret into `SENTRY_CLIENT_SECRET`. AgentOps turns the event's stack trace into a traceback and handles it like any other error.

### Optional: GitHub webhook (merge tracking + review-driven revisions)
In the repository: **Settings → Webhooks → Add webhook**, payload URL `https://<agentops-host>/webhooks/github`, content type `application/json`, secret = `GITHUB_WEBHOOK_SECRET`, events **Pull requests**, **Pull request reviews**, **Issue comments**. For a local server, expose it with a tunnel such as `cloudflared tunnel --url http://localhost:8000`.

### 3. Review and decide

```bash
curl localhost:8000/incidents?project=orders-api -H "X-API-Key: $KEY"
curl localhost:8000/incidents/12 -H "X-API-Key: $KEY"        # root cause, diff, test logs, steps, audit
curl -X POST localhost:8000/incidents/12/approve -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"approver": "muhammad", "note": "LGTM"}'                 # → {"pr_url": "..."}
curl -X POST localhost:8000/incidents/12/reject  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"approver": "muhammad", "reason": "fix belongs in the validator"}'
```

## API

| Method | Path | Purpose |
|---|---|---|
| POST / GET | `/projects` | Register / list repositories |
| GET / PATCH / DELETE | `/projects/{name}` | Detail (incl. setup log) / update / delete (no incidents) |
| POST | `/projects/{name}/setup` | Re-sync and reinstall dependencies |
| POST | `/ingest/logs` | `{project, source, lines[]}` |
| POST | `/ingest/events` | `{project, source, events: [{traceback, method, path, service, message}]}` |
| POST | `/ingest/file` | multipart: `project`, `file` |
| GET | `/incidents?project=&status=` | List |
| GET | `/incidents/{id}` | Detail |
| GET | `/incidents/stream`, `/incidents/{id}/stream` | Server-Sent Events (`?api_key=` allowed here, since EventSource can't send headers) |
| POST | `/incidents/{id}/approve` · `/reject` · `/run` | Human decision; re-run a `FAILED`/`NEEDS_HUMAN` incident |
| GET | `/metrics?project=` | Incident counts, verified-fix and first-attempt rates, time to fix / PR / merge, token use |
| POST | `/webhooks/github` | PR closed/merged, "changes requested" reviews, `/agentops` comments (HMAC) |
| POST | `/webhooks/sentry/{project}` | Sentry error events (HMAC or API key) |
| GET | `/status` · `/health` | Configured integrations · liveness (no auth) |

## Tests

```bash
cd backend && python -m pytest -q        # 40 tests, under a minute (one test pip-installs into a real venv)
```

The pipeline tests run AgentOps as a **real HTTP server**, register a **real git repository** (`tests/fixtures/sample_service`, a small service with known bugs, committed into a temp repo under `services/orders`), let AgentOps clone it, execute the service's code in a separate process, ship the real exception with the bundled handler over HTTP, and run the fixture's pytest suite in the sandbox. The lifecycle test goes all the way: real exception → fix + regression test verified in the sandbox → approval → PR → HMAC-signed "changes requested" review → revision committed to the PR branch → merge webhook → `RESOLVED`; the next occurrence is flagged as a regression and the earlier fix is fed to the Investigator. Stand-ins: the **LLM** (scripted tool calls in `tests/fake_llm.py`), the **GitHub REST API** and **Slack** (`httpx.MockTransport`). So the tests prove the plumbing, not Nemotron's diagnostic quality.

Live check (27 Oct 2026, `nvidia/Nemotron-3-Ultra-550b-a55b` on Token Factory, before regression tests were required): the three bugs in `tests/fixtures/sample_service` were each diagnosed and fixed on the first attempt, verified by pytest and opened as real GitHub PRs.

## Status

| Area | Status |
|---|---|
| Projects: clone/sync (token via header), per-project venv install | Implemented, tested against a real local git repo |
| Ingest (logs, events, file), Detector, grouping, severity | Implemented, tested |
| Python log-shipping handler | Implemented, tested end to end over HTTP |
| Tool loop, Investigator, Fixer | Implemented; run live on Nemotron 3 Ultra (3/3 sample bugs fixed on the first attempt) and covered by scripted tests |
| Tavily | Implemented and configured in the live run |
| Sandbox: subprocess | Tested. Sandbox: Docker — implemented, not tested here (no Docker daemon) |
| GitHub clone over https, real PR | Done live: public repo clone and 3 real PRs. Private-repo clone implemented, not yet tried |
| API-key auth, restart recovery, double-approval guard | Implemented, tested |
| Regression-test requirement, review-driven revisions, merge tracking, memory, regression detection, metrics, notifications | Implemented, tested end to end with scripted model output and mocked GitHub/Slack; not yet run live |
| Sentry webhook | Implemented against Sentry's documented event payload; not tested against a live Sentry account |
| Email notifications | Implemented (SMTP); not tested here |
| Schema upgrades | Additive auto-migration on startup (new columns are added to existing tables; existing data kept) |
| Not built | CloudWatch/Loki connectors, non-Python repos, Alembic |

## Deploy

`backend/Dockerfile` (build from repo root). Mount a volume at `/data` so clones and venvs survive restarts, set `DATABASE_URL` to Postgres for durable history, and set `API_KEYS`. Subprocess sandboxing runs project tests on the server itself: register only repositories you trust, or use `SANDBOX_MODE=docker` on a host with Docker.

## Layout

```
backend/app/
  main.py          app, auth wiring, startup recovery
  config.py        settings (env / .env)
  auth.py          API-key check
  projects.py      clone/sync, venv setup, per-project context
  context.py       project bound to the current pipeline run
  llm.py           Token Factory client + tool-calling loop
  orchestrator.py  state machine, retries, approval gate, PR body
  agents/          detector, investigator, fixer, tester, prompts, step recorder
  tools/           repo (read-only), search (Tavily), patching, sandbox, github
  routers/         projects, ingest, incidents (+SSE)
backend/scripts/check_nebius.py
backend/tests/     unit + end-to-end tests, fixtures/sample_service
clients/python/agentops_handler.py
sandbox/Dockerfile
```

The dashboard is built separately against this API; see **[docs/API.md](docs/API.md)** for every endpoint, real response shapes, the incident lifecycle and live updates (SSE).

## License

MIT, see [LICENSE](LICENSE).
