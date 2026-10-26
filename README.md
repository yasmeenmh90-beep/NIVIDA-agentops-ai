# AgentOps AI

An autonomous AI incident engineer. It reads your error logs, finds the root cause in the code, writes a minimal patch, proves it with your test suite in a sandbox, and then **waits for a human to approve** before opening a GitHub pull request. It never merges and never touches production.

Built for the **Nebius × NVIDIA Global AI Hackathon 2026**, track *Coding and Agentic Engineering*. All reasoning runs on **NVIDIA Nemotron via Nebius Token Factory**; the Investigator uses **Tavily** for web evidence.

```
logs ─► Detector ─► Investigator ─► Fixer ─► Tester ─► Approval gate ─► GitHub PR
        (rules +     (Nemotron +     (Nemotron)  (pytest in   (a human)     (only on Approve)
        Nemotron     repo tools +       ▲         sandbox)
        summary)     Tavily)            └─ tests fail: retry with the failure output (max 3)
```

Incident states: `DETECTED → INVESTIGATING → FIXING → TESTING → AWAITING_APPROVAL → PR_CREATED | REJECTED`, plus `NEEDS_HUMAN` (no patch passed after 3 attempts) and `FAILED` (pipeline error, can be re-run).

## How it works

| Agent | What it does | Model |
|---|---|---|
| **Detector** | Parses JSON or plain-text Python tracebacks, maps frames to repo files, groups repeats by signature (`exception + file:function`), assigns severity (write endpoints = high, escalates with occurrences). Then asks the small model for a one-line summary. | rules + Nemotron (triage) |
| **Investigator** | Native tool-calling loop with `list_files`, `read_file`, `search_code`, `git_log`, `web_search` (Tavily). Must finish with `submit_root_cause` (file, line, explanation, evidence, confidence). | Nemotron (reasoning) |
| **Fixer** | Returns exact search/replace edits via `submit_patch`. The backend validates them (unique match, `.py` only, no protected paths, no edits to existing tests, ≤ 40 changed lines) and generates the unified diff itself, so the diff a human sees is exactly what gets applied. Invalid edits are sent back to the model to correct. | Nemotron (reasoning) |
| **Tester** | Copies the repo to a throwaway directory, runs pytest before and after the patch, parses JUnit XML. **Verified** = at least one previously failing test now passes and no new failures. If the Fixer adds a regression test, it must fail on the old code first. | none |
| **Approval gate** | Not an LLM. The pipeline stops; the dashboard shows root cause, diff, test proof and an Approve / Reject bar. Approve creates a branch, commits the files and opens a PR whose body contains the diagnosis, diff, test results and approver. | none |

### Safety
- No auto-merge. PRs only after a named human approves; every decision is stored.
- Agents can only read files and propose edits. No shell access for the model.
- Paths are confined to the target repo (traversal and absolute paths rejected).
- Protected paths (CI, Dockerfile, requirements, config, `tests/conftest.py`) cannot be patched; existing tests cannot be edited.
- Tests run on a copy: Docker with `--network none`, memory/CPU/pid limits, or a subprocess with a scrubbed environment (no API keys) and a timeout.
- Log lines, file contents and web results are wrapped in `<untrusted_*>` tags; prompts tell the model to treat them as data.
- Per-incident token budget, tool-round cap, full audit log (`audit_log` table) of every tool call, status change and decision.

## Quick start (local)

Requirements: Python 3.11+, Node 20+.

```bash
# 1. Backend
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                    # add NEBIUS_API_KEY (+ TAVILY_API_KEY, GITHUB_*)
python scripts/check_nebius.py                          # lists Nemotron models + tests tool calling
uvicorn app.main:app --reload --port 8000

# 2. Frontend (new terminal)
cd frontend
npm install
cp .env.example .env                                    # VITE_API_URL=http://localhost:8000
npm run dev                                             # http://localhost:5173
```

Click **Checkout without a discount code** in the dashboard. An incident appears and the agents start.

The demo orders API runs **inside the backend** by default (`EMBED_DEMO_APP=true`), mounted at `/demo-app`, so one service is enough. To run it separately:

```bash
cd demo-app && pip install -r requirements.txt
AGENTOPS_INGEST_URL=http://localhost:8000/ingest/logs uvicorn orders.main:app --port 8001
# backend/.env: DEMO_APP_URL=http://localhost:8001
python traffic.py --bug all            # or click the buttons in the dashboard
```

### Docker sandbox (optional, stronger isolation)
```bash
docker build -f sandbox/Dockerfile -t agentops-sandbox:latest .
# SANDBOX_MODE=auto picks Docker when this image exists, else subprocess
```

### GitHub PRs
Create a fine-grained token limited to **one** repository with *Contents* and *Pull requests* read/write. Set `GITHUB_TOKEN`, `GITHUB_REPO=owner/name` and `GITHUB_PATH_PREFIX` (where the demo app lives in that repo; `demo-app` if you push this monorepo). AgentOps never modifies the local demo code, so the bugs stay in place for the next demo run.

## Demo target

`demo-app/` is a small FastAPI orders service with three planted bugs, each caught by a failing test:

| # | Bug | Symptom | Failing test(s) |
|---|---|---|---|
| 1 | `discount_rate()` returns `None` for a missing code | `TypeError` on `POST /orders` | `test_create_order_without_discount` |
| 2 | Serializer reads `customer_id` after the store renamed it to `customer_ref` | `KeyError: 'customer_id'` on `GET /orders/{id}` | `test_get_order_returns_customer_id` |
| 3 | `start = page * size` instead of `(page - 1) * size` | `IndexError` on `GET /orders?page=last` | `test_first_page_starts_at_first_order`, `test_last_page` |

Because all three bugs are present at once, a fix for bug 1 leaves the other tests failing. The Tester therefore checks "fixes at least one failing test, breaks nothing" and reports the unrelated failures separately.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/ingest/logs` | `{"source": "...", "lines": ["..."]}` |
| POST | `/ingest/file` | Upload a log file |
| GET | `/incidents` | List incidents |
| GET | `/incidents/stream` | SSE: incident list updates |
| GET | `/incidents/{id}` | Detail: root cause, patches, test logs, steps, decisions, audit |
| GET | `/incidents/{id}/stream` | SSE: live detail updates |
| POST | `/incidents/{id}/approve` | `{"approver", "note"}` → branch + PR |
| POST | `/incidents/{id}/reject` | `{"approver", "reason"}` |
| POST | `/incidents/{id}/run` | Re-run a `FAILED` / `NEEDS_HUMAN` incident |
| POST | `/demo/trigger/{1-3}` | Fire a planted bug |
| POST | `/demo/reset` | Clear incidents (demo only) |
| GET | `/status`, `/health` | Configured integrations, liveness |

## Tests

```bash
cd backend && python -m pytest -q
```

19 tests. The pipeline tests use the real Detector, tool loop, patch validation, sandboxed pytest on `demo-app`, retry logic, approval gate and PR request sequence. The LLM is replaced by a **scripted stand-in** (`tests/fake_llm.py`) and GitHub by an `httpx.MockTransport`, so they run offline. They prove the plumbing, not Nemotron's diagnostic quality.

`python scripts/offline_preview.py` runs the backend with that scripted stand-in (bug 1 only) for UI work without credits. Do not use it for the demo video.

## Project status

| Area | Status |
|---|---|
| Detector, de-duplication, severity | Implemented, unit-tested |
| Tool-calling loop, Investigator, Fixer | Implemented; tested with scripted model responses. **Not yet run against live Nemotron** (needs your key; run `scripts/check_nebius.py` first) |
| Tavily search | Implemented; not yet called with a real key |
| Sandbox (subprocess) | Implemented and exercised by tests |
| Sandbox (Docker) | Implemented; image build not tested in this environment |
| GitHub PR flow | Implemented; tested against a mock API, not a real repo yet |
| Dashboard (live SSE, diff, tests, timeline, approve/reject) | Implemented, built and checked in a browser |
| Nebius Token Factory sandboxes | Not used. The hackathon page does not mention them; Docker/subprocess is used instead |
| Stretch goals (Sentry/CloudWatch ingest, Slack, metrics page, JS/TS repos) | Not started |

## Deploy

- **Backend → Render**: `render.yaml` (Docker, built from repo root). Set `NEBIUS_API_KEY`, `TAVILY_API_KEY`, `GITHUB_TOKEN`, `GITHUB_REPO`, `CORS_ORIGINS`. Render has no Docker-in-Docker, so tests run in subprocess mode there. SQLite resets on redeploy; set `DATABASE_URL` to Postgres to keep history.
- **Frontend → Vercel**: root `frontend/`, env `VITE_API_URL=https://<your-render-app>.onrender.com`.

## Repository layout

```
backend/app/
  main.py            FastAPI app, CORS, /status
  config.py          all settings (env / .env)
  llm.py             Token Factory client + tool-calling loop
  orchestrator.py    state machine, retries, approval gate, PR body
  agents/            detector, investigator, fixer, tester, prompts, step recorder
  tools/             repo (read-only), search (Tavily), patching, sandbox, github
  routers/           ingest, incidents (+SSE), demo
backend/scripts/     check_nebius.py, offline_preview.py
backend/tests/       unit + end-to-end pipeline tests
demo-app/            orders API with 3 planted bugs, tests, traffic.py
frontend/src/        React + TypeScript + Tailwind dashboard
sandbox/Dockerfile   image for SANDBOX_MODE=docker
```

## License

MIT, see [LICENSE](LICENSE).
