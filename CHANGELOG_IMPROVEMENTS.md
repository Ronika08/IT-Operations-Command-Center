# CHANGELOG - Interview-Readiness Upgrade

This upgrade responded to a strict, independent code-level audit of the
existing project (summarized in `FINAL_IMPLEMENTATION_AUDIT.md`). Every
entry below is a real, tested code change - not a documentation-only
update - except where explicitly noted as docs-only.

---

## Priority 1 - SLA policy is now genuinely database-driven

**Old behavior:** `sla_rules` was a real, seeded, readable Postgres
table, but `detect_and_open_incident()` computed every incident's
deadlines from a hard-coded `DEFAULT_SLA_MINUTES` dict, ignoring the
table entirely. Editing a row had zero effect on real behavior. No write
endpoint existed for the table at all.

**New behavior:** `detect_and_open_incident()` fetches the live
`sla_rules` row for the incident's severity
(`app/sla/repository.py::get_sla_policy_for_severity`) and uses it,
falling back to the built-in default (with a logged warning) only if no
row exists. `PUT /sla/rules/{severity}` (ADMIN-only, audited) is the new
controlled write path.

**Files changed:** `app/sla/engine.py` (added `sla_minutes_from_rule`),
`app/sla/repository.py` (new), `app/ingestion/collector.py`
(`detect_and_open_incident` now calls the repository),
`app/routers/sla.py` (new `PUT /rules/{severity}`).

**Tests added:** `tests/unit/test_sla_repository.py` (5 tests, including
an end-to-end proof through the real `detect_and_open_incident` code
path), plus 2 new integration tests in `tests/integration/test_api.py`.

**Interview value:** the single strongest "I audited my own work and
found something real" story in this project - see
`docs/interview_story.md` and `docs/bug_reports.md` (BUG-008).

---

## Priority 2 - Real application log ingestion

**Old behavior:** the `logs` table only ever received one synthetic
line per incident, written by the collector itself ("Incident
auto-opened: ..."). No service emitted its own logs. Two of five RCA
rules (`_rule_downstream_db_timeout`, `_rule_connection_pool`) could
almost never fire from real data.

**New behavior:** each of the 4 services has a real, bounded (500-entry)
structured log buffer (`app/applog.py`) and a `/logs` endpoint. Real
events are logged at real moments: login attempts, order/payment
outcomes, every fault-injection state transition, and specifically the
DB-timeout (payments-service) and connection-pool-exhaustion
(db-proxy-service) scenarios, using close-to-verbatim the example
messages from the design brief. The central platform ingests new
entries every poll cycle via a durable per-service cursor
(`Service.last_ingested_log_seq`).

**Files changed:** `services/*/app/applog.py` (new, x4),
`services/*/app/main.py` (all 4, added `/logs` + log_event calls),
`app/models/db.py` (`Service.last_ingested_log_seq`),
`app/ingestion/collector.py` (`ingest_logs`, wired into `poll_all_services`
and `poll_now`).

**Tests added:** `tests/unit/test_service_logs.py` (8 tests) - proves,
against the real service code (not hand-written log fixtures), that the
DB-timeout and connection-pool scenarios now produce log lines that
independently drive the correct RCA category.

**Interview value:** demonstrates you can trace a symptom (RCA
undertested for two categories) to its real root cause (no real log
source existed) rather than patching the symptom (e.g. loosening the
rule's matching criteria).

---

## Priority 3 - Automatic recovery detection

**Old behavior:** nothing detected a service coming back healthy; an
incident could only be closed by a human clicking Resolve, even long
after the underlying service had recovered.

**New behavior:** a new `RECOVERED` incident status + `recovered_at`
timestamp. `app/ingestion/collector.py::check_recovery()` runs every
poll cycle; if an active incident's triggering condition no longer
breaches, it's marked `RECOVERED` (system fact, audited with
`user_id=NULL`), fires exactly once, and remains distinct from
`RESOLVED` (a human's separate, later action). `ACTIVE_INCIDENT_STATUSES`
excludes `RECOVERED`/`RESOLVED`, so a relapse opens a genuinely new
incident rather than being silently absorbed into the old one.

**Files changed:** `app/models/db.py` (`IncidentStatus.RECOVERED`,
`Incident.recovered_at`, `ACTIVE_INCIDENT_STATUSES`),
`app/ingestion/collector.py` (`_evaluate_breach` factored out,
`check_recovery` new), `app/routers/incidents.py` (`IncidentOut` exposes
`recovered_at`; `_refresh_breach_flags` updated to still allow
`RECOVERED -> BREACHED`).

**Tests added:** `tests/unit/test_recovery_detection.py` (7 tests) + 1
integration test proving the field is visible through the real API.

**Interview value:** a clean example of deliberately modeling two
distinct facts (system-observed recovery vs. human-declared resolution)
instead of collapsing them, which is exactly the kind of distinction a
service-management process cares about.

---

## Priority 4 - Real authentication + RBAC

**Old behavior:** a `users` table and `UserRole` enum existed but were
never used by any code path - no login endpoint, no auth dependency, no
role check anywhere. Every `AuditLog.user_id` was `NULL` because callers
could only optionally supply an arbitrary, unverified id.

**New behavior:** real login (`POST /auth/login`), PBKDF2 password
hashing, JWT issuance/verification, 4 roles matching real operational
responsibilities (ADMIN/IT_SUPPORT/INCIDENT_MANAGER/RCA_REVIEWER), and
every state-changing endpoint protected by `require_roles(...)`. Audit
logs now carry the real authenticated user's id for every human action.

**Files changed:** `app/core/security.py` (new), `app/core/deps.py`
(new), `app/routers/auth.py` (new), `app/models/db.py` (`UserRole`
replaced, `User.role` default changed), `app/main.py` (seeds 4 demo
users from env vars, logs a warning on default passwords), every router
(`incidents.py`, `services.py`, `sla.py`, `conversion.py`, `audit.py`)
now requires `get_current_user`/`require_roles`. `AckRequest`/
`ResolveRequest`/`VerifyRequest` no longer accept a caller-supplied
`user_id` at all - a deliberate, intentional breaking API change.

**Tests added:** `tests/unit/test_security_auth.py` (8 tests) + 10 new
auth/RBAC integration tests in `tests/integration/test_api.py`.

**Interview value:** the clearest, most literal answer to "tell me about
a time you added security controls to an existing system."

---

## Priority 5 - AI-explanation validation layer

**Old behavior:** the LLM's prompt asked it not to invent information,
but nothing checked whether it actually complied - grounding was
prompt-level only.

**New behavior:** `app/rca/ai_validator.py` runs a deterministic check
on every LLM explanation: flags a mentioned metric/number not present in
the evidence it was given, and flags overconfident language when the
rule engine reported `undetermined`. Result stored as
`Recommendation.ai_validation_status` (PASSED/FLAGGED/UNAVAILABLE) +
`ai_validation_reason`.

**Files changed:** `app/rca/ai_validator.py` (new), `app/models/db.py`
(`Recommendation.ai_validation_status`/`ai_validation_reason`),
`app/routers/incidents.py::run_rca` (wired in).

**Tests added:** `tests/unit/test_ai_validator.py` (7 tests) - no live
LLM call required, and none of the integration tests need one either
(`LLM_ENABLED=false`).

**Interview value:** shows the difference between "asking an LLM nicely"
and "actually checking what it did" - a distinction worth drawing out
explicitly if hallucination comes up.

---

## Priority 6 - Pagination + the write-amplification fix

**Old behavior:** `GET /incidents` returned every row, unbounded, and
`_refresh_breach_flags` committed to the database once *per incident*
on every single read - meaning every dashboard refresh issued as many
write transactions as there were total historical incidents.

**New behavior:** `GET /incidents?page=1&limit=20` returns
`{items, page, limit, total}` (max limit 100). `_refresh_breach_flags`
now mutates in memory and commits **once per request**, regardless of
how many incidents are on the page.

**Files changed:** `app/routers/incidents.py` (`list_incidents`,
`_refresh_breach_flags`), `frontend/js/app.js`/`index.html` (pagination
controls + updated response-shape handling).

**Tests added:** 2 new integration tests
(`test_incidents_pagination_pages_and_total_are_consistent`,
`test_incidents_pagination_limit_over_max_is_rejected`).

**Interview value:** a concrete, quantifiable "found a real O(n)
write-amplification bug and fixed it" story for a scalability question.

---

## Priority 7 - CI/CD

**New:** `.github/workflows/ci.yml` - runs the unit suite, the
integration suite against a real ephemeral Postgres service container,
and builds all 5 Docker images, on every push/PR. `LLM_ENABLED=false`
throughout, so CI never needs a real Gemini key. **Not run** in this
build environment (no GitHub Actions runner available) - see
`docs/testing.md` for exactly what that means.

---

## Security cleanup (this upgrade)

- Removed dead `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL` settings from
  `core/config.py` (never used - the real code has always called
  Gemini) and centralized `GEMINI_API_KEY`/`GEMINI_MODEL` into
  `Settings` instead of being read via `os.getenv` directly in
  `llm_explainer.py`.
- Fixed the same stale "Anthropic" reference in `.env.example`,
  `docs/deployment.md`, and `docs/architecture.md`.
- Confirmed `.env` stays gitignored and out of this ZIP; `.env.example`
  contains placeholders only.
- All previously-fixed security items (admin-token gating, input
  validation, frontend XSS escaping) were re-verified, not undone - see
  `docs/bug_reports.md`.

## Documentation updated this upgrade (docs-only changes)

`README.md`, `docs/architecture.md`, `docs/business_requirements.md`,
`docs/failure_injection.md`, `docs/rca_reports.md`, `docs/bug_reports.md`,
`docs/deployment.md` - all updated to describe the current implementation
accurately, including removing the stale "Anthropic" and
"SLARule-drives-deadlines" claims that didn't match the code even before
this upgrade started. New: `docs/security.md`, `docs/testing.md`,
`docs/interview_story.md`, `docs/applied_materials_jd_mapping.md`.

## What was deliberately NOT changed

- The 4-service architecture, the rule-based RCA engine's actual rules,
  the LLM-explanation-layer design, Prometheus/Grafana, Docker Compose
  as the orchestration choice, the vanilla JS frontend (extended, not
  replaced), and the CSV-conversion feature's core logic - all were
  already correctly designed; only their auth-gating and (for RCA) their
  evidence quality changed. See `FINAL_IMPLEMENTATION_AUDIT.md` for the
  full list.
