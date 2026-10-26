# AgentOps AI

An autonomous incident engineer for Python services. Your app ships its exceptions to AgentOps; AgentOps finds the root cause in your repository, writes a minimal patch, proves it with **your own test suite** in a sandbox, and then **waits for a human to approve** before opening a GitHub pull request. It never merges and never pushes to your branches.

All reasoning runs on **NVIDIA Nemotron via Nebius Token Factory**; the Investigator can use **Tavily** for web evidence. Built for the Nebius × NVIDIA Global AI Hackathon 2026 (track: Coding and Agentic Engineering).

```
your service ──(agentops_handler / HTTP)──► Detector ─► Investigator ─► Fixer ─► Tester ─► Approval gate ─► GitHub PR
                                          rules +      Nemotron +      Nemotron   your pytest   a human       (on Approve)
                                          Nemotron     repo tools +       ▲       in sandbox
                                          summary      Tavily             └── tests fail: retry with output (max 3)
```

States: `DETECTED → INVESTIGATING → FIXING → TESTING → AWAITING_APPROVAL → PR_CREATED | REJECTED`, plus `NEEDS_HUMAN` (no patch passed after 3 attempts; nothing is proposed) and `FAILED` (pipeline error; can be re-run).

## How it works

1. **Register a repository** (`POST /projects`). AgentOps clones it into its own workspace and installs its dependencies into a per-project virtualenv (`requirements*.txt` or `pyproject.toml`, or your own `install_command`).
2. **Ship errors** from your service with the bundled handler (`clients/python/agentops_handler.py`), or POST log lines / structured events.
3. **Detector** parses Python tracebacks (JSON log lines or plain text), maps frames to files in your repo, groups repeats by `exception + file:function`, and sets severity (write endpoints are high; escalates with occurrences).
4. Before investigating, AgentOps **hard-resets its clone to the latest default branch** and records the commit sha.
5. **Investigator** (Nemotron, native tool calling): `list_files`, `read_file`, `search_code`, `git_log`, `web_search` → `submit_root_cause` with file, line, explanation, evidence, confidence.
6. **Fixer** (Nemotron) returns exact search/replace edits. AgentOps validates them and builds the unified diff itself, so the diff you review is exactly what gets applied. Invalid edits go back to the model to correct.
7. **Tester** runs your suite on a throwaway copy before and after the patch. **Verified** = at least one previously failing test now passes and no test newly fails. A regression test added by the Fixer must fail on the old code first.
8. **Approval gate** (a human, not an LLM): Approve opens a PR on a new branch created from the analysed commit, with diagnosis, diff, test results and approver in the body. Reject closes the incident with a reason.

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
| GET | `/status` · `/health` | Configured integrations · liveness (no auth) |

## Tests

```bash
cd backend && python -m pytest -q        # 26 tests, ~1.5 min (one test pip-installs into a real venv)
```

The pipeline tests run AgentOps as a **real HTTP server**, register a **real git repository** (`tests/fixtures/sample_service`, a small service with known bugs, committed into a temp repo under `services/orders`), let AgentOps clone it, execute the service's code in a separate process, ship the real exception with the bundled handler over HTTP, and run the fixture's pytest suite in the sandbox. Two things are stand-ins: the **LLM** (scripted tool calls in `tests/fake_llm.py`) and the **GitHub API** (`httpx.MockTransport`). So the tests prove the plumbing, not Nemotron's diagnostic quality.

## Status

| Area | Status |
|---|---|
| Projects: clone/sync (token via header), per-project venv install | Implemented, tested against a real local git repo |
| Ingest (logs, events, file), Detector, grouping, severity | Implemented, tested |
| Python log-shipping handler | Implemented, tested end to end over HTTP |
| Tool loop, Investigator, Fixer | Implemented, tested with scripted model output. **Not yet run on live Nemotron** — run `scripts/check_nebius.py` with your key first |
| Tavily | Implemented; not yet called with a real key |
| Sandbox: subprocess | Tested. Sandbox: Docker — implemented, not tested here (no Docker daemon) |
| GitHub clone of a private repo over https, real PR | Implemented; tested only against a local repo and a mocked API |
| API-key auth, restart recovery, double-approval guard | Implemented, tested |
| Not built | Sentry/CloudWatch/Loki connectors, non-Python repos, Alembic migrations (schema is created with `create_all`; delete the SQLite file after upgrading) |

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

The `frontend/` dashboard from the first version is unchanged and still calls the removed demo endpoints; it needs updating for projects and API keys.

## License

MIT, see [LICENSE](LICENSE).
