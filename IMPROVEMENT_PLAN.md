# IMPROVEMENT_PLAN.md

Priorities follow the audit in `AUDIT_REPORT.md`. P0/P1 items were
implemented in this session (see status column); P2/P3 are documented
honestly as **not** implemented, with the reasoning for deferring each
one, rather than rushed in to inflate a feature count.

## P0 — Must Fix

| # | Item | Status | Notes |
|---|---|---|---|
| P0-1 | Actually run `docker compose up` end-to-end on a machine with Docker, capture real output, and update `docs/test_plan.md`'s "Known Gaps" section accordingly | **NOT DONE - could not be done in this sandbox** (no Docker daemon available; verified with `which docker` → not found). This is the single highest-priority remaining action and should be done before the interview, on a laptop with Docker installed, using the exact commands in `docs/deployment.md` |
| P0-2 | Fix unauthenticated `/admin/*` fault-injection endpoints (BUG-005) | **DONE** - `FAULT_INJECTION_TOKEN` + `X-Admin-Token` header on all 4 services, 19 regression test cases, live-verified with curl |
| P0-3 | Fix dead `AVAILABILITY_THRESHOLD` config (BUG-004) | **DONE** - wired to `settings.AVAILABILITY_THRESHOLD`, 3 regression tests |
| P0-4 | Fix false "36 unit tests" claim in README/test_plan/skill_matrix | **DONE** - corrected to the real, verified number (55, post-audit) in all 3 files, with the original discrepancy disclosed rather than hidden |
| P0-5 | Fix stored XSS in the frontend dashboard (BUG-007) | **DONE** - `escapeHtml()` applied to every interpolated field; no automated regression test exists for this (no headless-browser tooling in this environment) - see P2-3 |

## P1 — High Value

| # | Item | Status | Notes |
|---|---|---|---|
| P1-1 | Fix `db-proxy-service /records` raw-query-param API inconsistency (BUG-006) | **DONE** - real Pydantic body, length-bounded, 4 regression tests |
| P1-2 | Add SLA boundary-condition tests (exact-deadline, one-second-late, already-resolved stays resolved) | **DONE** - 5 new tests in `tests/unit/test_sla_engine.py` |
| P1-3 | Add regression tests proving the incident-detection threshold fix actually changes behavior | **DONE** - `tests/unit/test_incident_detection.py`, 3 tests, in-memory SQLite |
| P1-4 | Re-run the full test suite after every fix and record real pass/fail counts | **DONE** - 55 unit + 8 integration = 63/63, re-run multiple times in this session |

## P2 — Useful (not implemented this session - reasoning given)

| # | Item | Why deferred |
|---|---|---|
| P2-1 | Wire real authentication: a minimal login endpoint on the central platform that checks a hashed password against the `users` table and issues a real session/token, so `AuditLog.user_id` is populated from a real caller instead of always `NULL` | This is a genuinely sized feature (password hashing, session/token issuance, wiring every state-changing endpoint to require it, migrating the `AckRequest`/`ResolveRequest`/`VerifyRequest` models), not a quick patch. Rushing a shallow version just to fill a DB column would itself become a new audit finding ("half-implemented auth"). Scoped honestly as a real next feature, not done here |
| P2-2 | Migrate `datetime.utcnow()` → timezone-aware `datetime.now(UTC)` throughout `central-platform/app/` | This removes cosmetic Python 3.12 deprecation warnings, but the DB columns are `TIMESTAMP` (naive), not `TIMESTAMPTZ`. Mixing timezone-aware Python datetimes with naive DB columns risks a real `TypeError` on comparison (`can't compare offset-naive and offset-aware datetimes`) if done incompletely, and verifying it fully would need re-testing every SLA/incident code path across a timezone boundary - out of scope for a same-session fix under "don't rewrite working code merely for style." Deferred, not ignored - flagged as a real (if low-severity) piece of tech debt |
| P2-3 | Add a headless-browser (e.g. Playwright) regression test for the BUG-007 XSS fix | No browser-automation tooling is available in this environment; the fix itself was verified by code review (every interpolation site now passes through `escapeHtml()`), which is disclosed as manual verification in `docs/bug_reports.md` rather than claimed as automated |
| P2-4 | Auth-service's `USERS_DB` (plaintext dict) should hash passwords and, ideally, be backed by the real `users` table instead of being a second, disconnected user store | Depends on P2-1 being done first - fixing this in isolation would still leave two disconnected "user" concepts, just with one of them hashed |
| P2-5 | Actually re-run the Postman collection with `newman` and the Locust load test, and refresh `tests/load/results/*.csv` | Requires a running stack (all 5 services + Postgres), which requires Docker (see P0-1) - bundle into the same "run it for real on a Docker-capable machine" pass |

## P3 — Optional (explicitly not pursued)

| # | Item | Why not pursued |
|---|---|---|
| P3-1 | Add more backend services / a message queue / Kubernetes | Directly contradicts the project's own stated scope discipline (`docs/business_requirements.md`'s "Out of Scope" section, `docs/architecture.md`'s "Why this shape, not something bigger") and the audit brief's explicit instruction not to add technologies just to lengthen a list |
| P3-2 | Train an ML model for RCA classification | Same reasoning - the project's rule-based approach is *more* defensible for a 4-service system with no labeled failure data, not less; "why not ML" is already a strong, arguable interview answer as-is |
| P3-3 | Rewrite the frontend in React | The vanilla-JS dashboard is a legitimate, explicitly-allowed tech choice per the original spec ("React OR HTML/CSS/JS") and works correctly after the XSS fix; rewriting it would be effort spent on a preference, not a defect |
