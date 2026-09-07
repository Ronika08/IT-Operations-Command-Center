# Testing Strategy

## Test pyramid actually present in this repo

| Layer | Location | What it covers | Needs |
|---|---|---|---|
| Unit | `tests/unit/` | SLA math + DB-driven policy, RCA rules, AI-explanation validator, recovery detection, password hashing + JWT, CSV validation, Prometheus text parsing, per-service admin-token auth, per-service structured logging | Nothing external - pure Python + an in-memory SQLite DB where a DB is needed at all |
| Integration | `tests/integration/` | Full router -> DB -> response cycle: auth/login, RBAC on every protected endpoint, incident lifecycle, RCA endpoint, pagination, SLA rule updates, CSV import, audit trail, recovery via the real code path | A real Postgres (or an adapted substitute - see below) |
| Performance | `tests/load/locustfile.py` + `tests/load/results/*.csv` | Real Locust run against `orders-service` (891 requests, 0 failures, real percentile latencies checked into the repo) | Locust, a running stack |
| Manual/API smoke | `tests/postman_collection.json` | Endpoint-by-endpoint smoke test, matches the real API surface | Postman/newman, a running stack |
| Acceptance | `docs/failure_injection.md`'s demo sequence + `docs/rca_reports.md`'s drills | End-to-end scenario walkthroughs against the real running stack | Docker Compose |

## What was actually (re-)executed during this upgrade, and how

Being specific here matters more than a pass/fail summary - see
`FINAL_IMPLEMENTATION_AUDIT.md` for the full, itemized status table.
Short version:

- **Unit tests:** executed directly, unmodified environment assumptions
  (SQLite in-memory / a temp file), **89/89 passing**, including 34 new
  tests added for this upgrade's 6 priorities.
- **Integration tests:** the test file's own docstring says it targets a
  real Postgres. No Docker/Postgres was available in the sandboxed
  environment this upgrade was built in, so it was run against a local
  SQLite file substituted via `DATABASE_URL` instead - **24/24 passing**,
  but this is an *adapted*, not full-fidelity, verification of exactly
  what the CI workflow (`.github/workflows/ci.yml`) runs against a real
  ephemeral Postgres container. The CI workflow itself was authored but
  **not run** in this environment (no GitHub Actions runner available) -
  its syntax mirrors the exact commands that were run locally.
- **Docker builds:** **not run** in this environment (no Docker daemon
  available) - `docker-compose.yml` and all 5 Dockerfiles were reviewed
  for correctness by inspection, and the CI workflow builds all 5 images
  on every push once this repo is actually pushed to GitHub.
- **Load test / Postman collection:** **not re-run** this upgrade - the
  checked-in Locust results predate it, and neither exercises the newly
  auth-gated endpoints yet (a known follow-up, not a hidden gap - see
  `FINAL_IMPLEMENTATION_AUDIT.md`).
- **Frontend:** JS syntax-validated (`node --check`), every
  `getElementById` reference cross-checked against the HTML, and a full
  backend-side smoke test was run simulating the exact API call sequence
  the dashboard makes (login -> load dashboard data -> live SLA edit ->
  poll-now) - but no real browser/headless-browser test was run against
  the actual rendered page.

## New tests added this upgrade, by priority

| File | Priority | What it proves |
|---|---|---|
| `tests/unit/test_sla_repository.py` | 1 | Changing a `sla_rules` DB row changes the *next* incident's real deadlines; a missing row falls back safely |
| `tests/unit/test_service_logs.py` | 2 | Each service produces real structured logs at real events; the previously log-starved RCA rules now fire from those real logs, not hand-written fixtures |
| `tests/unit/test_recovery_detection.py` | 3 | Stays-down (no false recovery), recovers-once, RECOVERED != RESOLVED, relapse opens a new incident, system-attributed (NULL user) audit row |
| `tests/unit/test_security_auth.py` | 4 | Password hashing is salted/non-reversible, JWT round-trips, tampered/expired tokens are rejected |
| (auth/RBAC also covered in) `tests/integration/test_api.py` | 4 | Every protected endpoint rejects no-token and wrong-role, real audit `user_id` after login-gated actions |
| `tests/unit/test_ai_validator.py` | 5 | Unsupported metric/number claims and false confidence on `undetermined` are flagged; grounded explanations pass; no-LLM case reports `unavailable`, never `passed` |
| (pagination covered in) `tests/integration/test_api.py` | 6 | Page/limit/total are consistent, no cross-page overlap, over-max limit rejected |

## Why some things are intentionally not tested

- **Live Gemini calls:** `LLM_ENABLED=false` throughout unit/integration
  tests and CI - a live LLM call is non-deterministic and would make CI
  flaky and dependent on a real API key existing in every environment
  that runs these tests. The LLM call path itself (`app/rca/llm_explainer.py`)
  is exercised in its *fallback* branch by every RCA test; its success
  branch is reviewed by inspection, not integration-tested against the
  real API.
- **Frontend browser tests:** no Playwright/Selenium harness exists in
  this project - adding one is listed as a real, worthwhile follow-up in
  `FINAL_IMPLEMENTATION_AUDIT.md`, not silently skipped.
- **Multi-process/HA races** (e.g. two central-platform replicas racing
  on the duplicate-incident guard, discussed in `docs/architecture.md`'s
  scaling section): out of scope for a project that runs one replica by
  design; documented as a known limitation, not tested, because there is
  no multi-replica deployment to test it against.
