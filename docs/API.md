# AgentOps AI — Backend API guide (for the frontend)

Everything the dashboard needs, with real response shapes. The backend is FastAPI; interactive docs are at `GET /docs` and the machine-readable schema at `GET /openapi.json`.

## Basics

| | |
|---|---|
| Base URL (local) | `http://localhost:8000` |
| Auth | Every endpoint except `/health` and `/webhooks/*` needs an API key: header `X-API-Key: <key>` (or `Authorization: Bearer <key>`). Keys are listed in the backend's `API_KEYS` env var (comma-separated, so each person can have their own). |
| CORS | Allowed origins come from `CORS_ORIGINS` (default `http://localhost:5173`). Add your dev/prod frontend URL there. |
| Content type | JSON in, JSON out. |
| Timestamps | ISO 8601 in UTC, always ending in `Z` (e.g. `2026-10-27T14:36:19.646398Z`). |
| Errors | `{"detail": "message"}` with status `401` (bad key), `404` (not found), `409` (action not allowed in the current state), `422` (invalid input; `detail` is a list of `{loc, msg}`), `502` (GitHub call failed). Show `detail` to the user. |

```ts
const API = import.meta.env.VITE_API_URL;            // e.g. http://localhost:8000
const KEY = import.meta.env.VITE_AGENTOPS_KEY;       // one of API_KEYS
const res = await fetch(`${API}/incidents`, { headers: { "X-API-Key": KEY } });
```

## The incident lifecycle (drives most of the UI)

```
DETECTED → INVESTIGATING → FIXING ⇄ TESTING → AWAITING_APPROVAL → PR_CREATED → RESOLVED
                                         ↘ NEEDS_HUMAN          ↘ REJECTED    ↘ PR_CLOSED
                                                                 PR_CREATED ⇄ REVISING
any step → FAILED (can be re-run)
```

| status | Meaning | What the UI should offer |
|---|---|---|
| `DETECTED` | Error received, pipeline queued | spinner |
| `INVESTIGATING` | Investigator (Nemotron) is reading code | spinner, live tool calls |
| `FIXING` | Fixer is writing fix + regression test (`status_reason` says `attempt 2/3`) | spinner |
| `TESTING` | pytest running in sandbox | spinner |
| `AWAITING_APPROVAL` | Verified fix ready | **Approve** / **Reject** buttons, diff, test proof |
| `PR_CREATED` | PR open on GitHub (`pr_url`) | link to PR |
| `REVISING` | Reviewer asked for changes on GitHub; Fixer is updating the PR | spinner |
| `RESOLVED` | PR merged (`resolved_at`) | done |
| `PR_CLOSED` | PR closed without merge | done |
| `REJECTED` | Human rejected the fix | show reason |
| `NEEDS_HUMAN` | No fix passed verification after 3 attempts; nothing proposed | **Run again**, show attempts |
| `FAILED` | Pipeline error (`status_reason` has the error) | **Run again** |

"Running" statuses: `DETECTED`, `INVESTIGATING`, `FIXING`, `TESTING`, `REVISING`. The detail endpoint also has a boolean `running`.

---

## Projects (repositories AgentOps watches)

### `POST /projects` — register a repo
```json
{ "name": "shop", "repo_url": "https://github.com/acme/shop.git", "app_path": "", "default_branch": "main" }
```
Optional: `github_repo` (`owner/name`, derived from a github.com URL), `test_command`, `install_command`, `python_env` (`venv`|`system`), `sandbox_mode`, `protected_paths`. `name` must be lowercase letters/digits/`-`/`_`.
Returns `201` + project. Cloning and dependency install run in the background: poll `GET /projects/{name}` until `setup_status` is `ready` (or `failed`, then show `setup_log`).

### `GET /projects` → list · `GET /projects/{name}` → detail
```json
{
  "id": 1, "name": "shop", "repo_url": "https://github.com/acme/shop.git",
  "default_branch": "main", "app_path": "", "github_repo": "acme/shop", "github_token_env": "GITHUB_TOKEN",
  "test_command": "", "install_command": "", "python_env": "venv",
  "sandbox_mode": "", "sandbox_image": "", "protected_paths": "",
  "setup_status": "ready",                       // pending | running | ready | failed
  "head_sha": "a1495a1abc0733d7d51861677a8141ef59361532",
  "synced_at": "2026-10-27T14:36:19.435307Z", "created_at": "2026-10-27T14:36:19.400685Z",
  "setup_log": "checked out main at a1495a1abc07\n...",   // detail only
  "github_token_present": true,                           // detail only
  "effective_test_command": "python -m pytest -q -p no:cacheprovider",
  "effective_sandbox": "subprocess"
}
```
`PATCH /projects/{name}` (same fields as create, all optional) · `POST /projects/{name}/setup` (re-clone/reinstall) · `DELETE /projects/{name}` (`409` if it has incidents).

---

## Incidents

### `GET /incidents?project=shop&status=AWAITING_APPROVAL` — list (newest first, max 200)
Both query params optional.
```json
[{
  "id": 1, "project_id": 1, "commit_sha": "a1495a1abc0733d7d51861677a8141ef59361532",
  "title": "TypeError: unsupported operand type(s) for -: 'int' and 'NoneType' in order_total() on POST /orders",
  "summary": "POST /orders fails with TypeError when no discount code is sent.",   // one line, from Nemotron Nano
  "severity": "high",                      // low | medium | high | critical
  "status": "AWAITING_APPROVAL",
  "status_reason": "verified on attempt 1: 1 new regression test(s) fail before and pass after, 0 regressions",
  "error_signature": "TypeError|orders/pricing.py:order_total",
  "exception_type": "TypeError",
  "message": "unsupported operand type(s) for -: 'int' and 'NoneType'",
  "endpoint": "POST /orders",
  "service": "prod",
  "location": { "file": "orders/pricing.py", "line": 19, "function": "order_total" },   // where it crashed ({} if unknown)
  "occurrences": 1,                        // repeats of the same error are grouped here
  "tokens_used": 24479,
  "pr_number": null, "pr_url": "", "pr_branch": "",
  "revisions": 0,                          // reviewer-driven PR revisions so far
  "regression_of": null,                   // id of an earlier RESOLVED incident if this error came back
  "resolved_at": null,
  "first_seen": "2026-10-27T14:36:19.646398Z", "last_seen": "2026-10-27T14:36:19.646402Z",
  "updated_at": "2026-10-27T14:36:22.306981Z"
}]
```

### `GET /incidents/{id}` — everything for the detail page
All list fields, plus:

```jsonc
{
  "traceback": "Traceback (most recent call last):\n  File \"/app/orders/pricing.py\", line 19, ...",
  "running": false,

  "root_cause": {                           // null until the Investigator finishes
    "file": "orders/pricing.py", "line": 12, "function": "discount_rate",   // the line that must change
    "root_cause": "discount_rate() returns DISCOUNT_CODES.get(code), which is None when no discount code is sent. ...",
    "evidence": ["orders/pricing.py:12 `return DISCOUNT_CODES.get(code)` has no default", "..."],
    "fix_suggestion": "Return 0.0 when the code is missing or unknown.",
    "confidence": "high",                   // low | medium | high
    "tavily_sources": [{ "query": "...", "title": "...", "url": "https://..." }],   // web evidence (may be empty)
    "tool_rounds": 3
  },

  "similar_incidents": [                    // past fixes from this project given to the Investigator (may be empty)
    { "incident_id": 3, "score": 7.5, "status": "RESOLVED", "title": "...", "root_cause_file": "orders/pricing.py",
      "root_cause_line": 12, "root_cause": "...", "fix_explanation": "...", "diff": "...", "pr_url": "https://..." }
  ],

  "patches": [{                             // every attempt, oldest first; show the last one with tests_passed=true
    "id": 1, "attempt": 1,                  // revisions use attempt 101, 102... (revision 1), 201... (revision 2)
    "kind": "initial",                      // initial | revision
    "feedback": "",                         // reviewer comment that triggered a revision
    "explanation": "Missing or unknown discount codes now mean no discount instead of None.",
    "diff": "--- a/orders/pricing.py\n+++ b/orders/pricing.py\n@@ ...\n-    return DISCOUNT_CODES.get(code)\n+    return DISCOUNT_CODES.get(code, 0.0) if code else 0.0\n--- /dev/null\n+++ b/tests/test_regression_missing_discount.py\n...",
    "tests_passed": true,
    "regression_tests": ["tests/test_regression_missing_discount.py::test_order_without_discount_code"],
    "test_summary": {
      "verified": true,
      "problems": [],                       // human-readable reasons when verified=false
      "regression_tests": ["tests/test_regression_missing_discount.py::test_order_without_discount_code"],
      "non_reproducing_tests": [],          // new tests that already passed on the buggy code
      "fixed": ["tests/test_regression_missing_discount.py::test_order_without_discount_code",
                "tests/test_orders.py::test_create_order_without_discount"],
      "regressions": [],                    // tests that passed before and fail now
      "still_failing": ["tests/test_orders.py::test_last_page"],   // unrelated failures, before and after
      "before": { "passed": 2, "failed": 5 }, "after": { "passed": 4, "failed": 3 },
      "runner": "subprocess", "duration_s": 1.08,
      "before_failed": ["..."], "after_failed": ["..."]
    },
    "test_log": "=== BEFORE (unpatched + new regression tests) exit=1 ===\n... pytest output ...",   // long; show in a collapsible
    "created_at": "2026-10-27T14:36:21.900000Z"
  }],

  "steps": [{                               // agent timeline, oldest first
    "id": 3, "agent": "investigator",       // detector | investigator | fixer | tester | approval_gate
    "status": "done",                       // running | done | error
    "input_summary": "Traceback for TypeError at {...}",
    "model": "nvidia/Nemotron-3-Ultra-550b-a55b",
    "prompt_tokens": 1200, "completion_tokens": 240,
    "output": { /* agent-specific: root cause, diff, test summary, {"error": "..."} on failure, {"summary": "..."} for the triage step */ },
    "tool_calls": [{ "tool": "read_file", "args": { "path": "orders/pricing.py" },
                     "result_preview": "orders/pricing.py (lines 1-19 of 19)\n   1 | ...", "ts": "2026-10-27T14:36:20.1Z" }],
    "started_at": "2026-10-27T14:36:19.8Z", "finished_at": "2026-10-27T14:36:20.9Z"
  }],

  "decisions": [{ "id": 1, "decision": "approve", "reason": "LGTM", "decided_by": "yasmeen",
                  "pr_url": "https://github.com/acme/shop/pull/12", "decided_at": "..." }],

  "audit": [{ "id": 1, "actor": "detector", "action": "incident_opened",
              "detail": { "signature": "...", "source": "prod" }, "ts": "..." }]   // full log; actions include status_change, tool:<name>, repo_synced, approve_requested, pr_failed, revision_requested, revision_ignored
}
```

### Actions
| Request | Body | When | Result |
|---|---|---|---|
| `POST /incidents/{id}/approve` | `{"approver": "yasmeen", "note": "optional"}` | `AWAITING_APPROVAL` | `{"ok": true, "pr_url": "..."}`; status → `PR_CREATED`. `409` otherwise, `502` if GitHub fails. Disable the button while waiting (takes a few seconds). |
| `POST /incidents/{id}/reject` | `{"approver": "yasmeen", "reason": "required"}` | not running, no PR yet | `{"ok": true}`; status → `REJECTED` |
| `POST /incidents/{id}/run` | — | `FAILED`, `NEEDS_HUMAN`, `DETECTED` | `{"ok": true, "started": true}` |

PR revisions are triggered from GitHub (review "Request changes" or a `/agentops ...` comment on the PR), not from the dashboard.

---

## Live updates (Server-Sent Events)

No polling needed. `EventSource` can't send headers, so pass the key as `?api_key=` (accepted on `/stream` endpoints only).

```ts
const es = new EventSource(`${API}/incidents/stream?api_key=${KEY}&project=shop`);   // project optional
es.addEventListener("update", e => setIncidents(JSON.parse(e.data)));               // same shape as GET /incidents

const one = new EventSource(`${API}/incidents/${id}/stream?api_key=${KEY}`);
one.addEventListener("update", e => setDetail(JSON.parse(e.data)));                 // same shape as GET /incidents/{id}
```
An `update` event is sent immediately on connect and then whenever something changes (checked every second); keep-alive comments arrive every ~15 s. Close the stream when leaving the page.

---

## Ingesting errors (for a "send test error" button or docs)

`POST /ingest/logs`
```json
{ "project": "shop", "source": "prod",
  "lines": ["{\"method\":\"POST\",\"path\":\"/orders\",\"traceback\":\"Traceback (most recent call last):\\n  File \\\"/app/orders/pricing.py\\\", line 19, in order_total\\n    return round(subtotal * (1 - rate), 2)\\nTypeError: unsupported operand type(s) for -: 'int' and 'NoneType'\\n\"}"] }
```
→ `{"project": "shop", "events": 1, "new": [5], "updated": []}` (`new` = incident ids created; `updated` = existing incidents whose `occurrences` went up). Lines can also be plain-text tracebacks.
Also: `POST /ingest/events` (`{project, source, events: [{traceback, method, path, service, message}]}`), `POST /ingest/file` (multipart `project`, `file`). `409` if the project isn't `ready`.

---

## Metrics — `GET /metrics?project=shop`
```json
{
  "project": "shop",
  "incidents": { "total": 4, "by_status": { "PR_CREATED": 4 }, "by_severity": { "high": 1, "medium": 3 },
                 "regressions": 0, "total_occurrences": 4 },
  "fix_quality": { "verified_fix_rate": 1.0, "first_attempt_success_rate": 1.0, "avg_attempts_to_verified": 1,
                   "needs_human_now": 0, "ever_needed_human": 0, "approval_rate": 1.0, "merged": 0,
                   "revisions": { "total": 1, "verified": 1 } },
  "timing_minutes": {
    "detect_to_verified_fix": { "count": 4, "median": 0.51, "mean": 0.8, "p90": 1.62 },
    "detect_to_pr":           { "count": 4, "median": 1.4,  "mean": 7.04, "p90": 18.36 },
    "detect_to_merged_mttr":  { "count": 0, "median": null, "mean": null, "p90": null }
  },
  "llm_tokens": { "total": 83686, "per_incident": { "count": 4, "median": 24479.0, "mean": 27895.33, "p90": 36240.0 } }
}
```
Rates are 0–1 (`null` when there's no data yet).

## Status — `GET /status`
Which integrations are configured (no secrets). Useful for a header bar.
```json
{ "llm": { "configured": true, "provider": "Nebius Token Factory",
           "reasoning_model": "nvidia/Nemotron-3-Ultra-550b-a55b", "triage_model": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B" },
  "tavily": { "configured": true }, "auth_enabled": true,
  "webhooks": { "github": true, "sentry": false },
  "notifications": { "slack": false, "webhook": false, "email": false },
  "require_regression_test": true, "default_sandbox": "subprocess", "max_fix_attempts": 3, "auto_investigate": true }
```
`GET /health` → `{"status": "ok"}` (no auth).

## Suggested screens
1. **Incidents list** — SSE on `/incidents/stream`; badge by `status` and `severity`; show `occurrences`, `regression_of`.
2. **Incident detail** — SSE on `/incidents/{id}/stream`: pipeline stepper from `status`; root cause card (+ Tavily links, similar incidents); diff of the last verified patch with the regression test highlighted; test proof (`before`/`after`, `regression_tests`, `regressions`); agent timeline from `steps` (tool calls collapsible); Approve/Reject bar when `AWAITING_APPROVAL`; PR link and revision history (`patches` with `kind: "revision"` and their `feedback`).
3. **Projects** — list, register form, setup status/log.
4. **Metrics** — numbers from `/metrics`.
