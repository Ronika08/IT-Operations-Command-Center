# IT Operations Command Center

Real-service monitoring, incident management, SLA tracking, root-cause
assistance, and now real authentication - built for the Applied
Materials IT Solutions Management interview.

This is not a simulated dashboard. It runs 4 real containerized FastAPI
services, scrapes their real Prometheus metrics and real structured
logs, opens real incidents against real thresholds fetched from a real
database, detects real recovery, and runs an explainable RCA engine
against real correlated evidence with a real login-gated frontend.

## What's actually in here

- **4 backend microservices** (auth, orders, payments, db-proxy) - real
  business logic, real `/health` + `/metrics` (Prometheus format), real
  `/logs` (structured application events - see Priority 2 below), and
  real fault-injection admin endpoints for failure drills.
- **Central Platform** - polls all 4 services, ingests their real logs,
  opens incidents on real threshold breaches, calculates SLA deadlines
  from a **live, database-driven policy** (not a hard-coded default -
  see "What changed" below), detects **automatic recovery** when a
  service comes back healthy, runs a rule-based RCA engine, asks an LLM
  to explain the result, runs a **deterministic AI-explanation
  validator**, and requires human verification before anything counts
  as confirmed.
- **Real authentication** - JWT login, 4 roles (ADMIN / IT_SUPPORT /
  INCIDENT_MANAGER / RCA_REVIEWER), hashed passwords, role-gated
  endpoints, and audit logs that carry the real acting user's id.
- **PostgreSQL** - 8 real tables (users, services, incidents, logs,
  metrics, sla_rules, recommendations, audit_logs).
- **Prometheus + Grafana** - real scrape config, a real dashboard JSON
  whose PromQL queries reference this project's actual metric names.
- **One real data-conversion feature** - legacy incident CSV ->
  validate -> dedup -> insert, with a real conversion log. Now
  ADMIN-only and audited.
- **Frontend** - plain HTML/CSS/JS dashboard, no build step. Now has a
  real login screen, pagination, and shows recovery state + AI
  validation status.
- **Tests** - 89 unit tests + 24 integration tests (against a real
  database), a Postman collection, and a real Locust load test run
  (results checked into `tests/load/results/`). See `docs/testing.md`
  for exactly what was and wasn't re-executed during this upgrade, and
  under what substitutions.
- **CI** - `.github/workflows/ci.yml` runs both test suites (the
  integration suite against a real ephemeral Postgres service
  container) and builds all 5 Docker images on every push.

## What changed in this upgrade

This project went through a strict, code-level audit (see
`FINAL_IMPLEMENTATION_AUDIT.md`) that found several places where the
implementation didn't match what the architecture intended - most
importantly, **the `sla_rules` database table was seeded and readable
but had zero effect on real incident deadlines**, which were silently
computed from a hard-coded Python dict instead. This upgrade fixes that
and five other real gaps. Full before/after detail, file-by-file, is in
`CHANGELOG_IMPROVEMENTS.md`. Short version:

1. **SLA policy is now genuinely database-driven.** Changing a row in
   `sla_rules` (via the new `PUT /sla/rules/{severity}`, admin-only)
   changes the deadline of the *next* incident opened for that severity.
2. **Real application logs.** Each service now emits real structured
   log events (login attempts, order/payment outcomes, fault-injection
   transitions, DB-timeout/connection-pool-exhaustion errors) that the
   central platform ingests - the RCA rules that correlate logs with
   metrics can now actually fire from real data, not just in unit tests.
3. **Automatic recovery detection**, kept strictly distinct from human
   resolution (`RECOVERED` vs `RESOLVED` - a human still has to close
   the incident).
4. **Real auth/RBAC** replacing the previously-unused `users` table.
5. **A deterministic AI-explanation validator** layered on top of the
   existing grounded-prompt/human-verification design.
6. **Pagination** on `GET /incidents`, plus a real fix for the
   write-amplification bug the audit found (one DB commit per page
   instead of one per row).

## Quick start

```bash
cp .env.example .env
# edit .env: set a real JWT_SECRET_KEY and the 4 SEED_*_PASSWORD values
# for anything beyond a personal local demo (see docs/security.md)
docker compose up --build
```

Then open http://localhost:8080 (dashboard - sign in with one of the 4
seeded demo accounts, e.g. `admin` / whatever you set
`SEED_ADMIN_PASSWORD` to), http://localhost:8000/docs (API),
http://localhost:3000 (Grafana), http://localhost:9090 (Prometheus).

**Schema note:** if you're upgrading an existing running instance
rather than starting fresh, see `database/migrations/README.md` - the
app creates new tables automatically but does not `ALTER` existing ones,
so a fresh `docker compose down -v && docker compose up` (or running
`002_operational_upgrades.sql` by hand) is needed to pick up this
upgrade's schema changes.

See `docs/deployment.md` for full instructions, including how to run the
failure-injection drills and the test suite.

## Documentation index

| Doc | Contents |
|---|---|
| `docs/business_requirements.md` | Problem statement, stakeholders, functional/non-functional requirements, explicit scope boundaries |
| `docs/architecture.md` | System architecture, data flow, ER diagram, scaling discussion |
| `docs/api_docs.md` | Endpoint reference for all 5 services |
| `docs/security.md` | Auth/RBAC design, password/JWT choices, what's NOT production security |
| `docs/testing.md` | Test strategy + what was actually (re)executed in this upgrade, and how |
| `docs/bug_reports.md` | Real bugs found and fixed, full reproduce -> root cause -> fix -> regression trail |
| `docs/rca_reports.md` | Real failure-injection drills and what the RCA engine concluded (including an honest miss, and the fix) |
| `docs/failure_injection.md` | Playbook for triggering real faults in each service, incl. the recovery + SLA-live-edit demo |
| `docs/deployment.md` | Setup, environment variables, drill commands, current-vs-production deployment |
| `docs/skill_matrix.md` | Original JD requirement -> feature mapping |
| `docs/applied_materials_jd_mapping.md` | Updated, more detailed JD mapping with evidence + honest limitations |
| `docs/interview_story.md` | How to actually talk about this project in the interview |
| `CHANGELOG_IMPROVEMENTS.md` | This upgrade's changes, file-by-file, with reasons |
| `FINAL_IMPLEMENTATION_AUDIT.md` | What was changed/fixed/added/deliberately not changed, test results, honest score |

## Project structure

```
services/
  auth-service/       orders-service/    payments-service/   db-proxy-service/
    app/
      main.py     applog.py (Priority 2: real structured log buffer + /logs)
central-platform/
  app/
    core/       config, DB session, security (password hashing + JWT), auth deps
    models/     SQLAlchemy models (8 entities)
    routers/    auth, incidents, services, sla, conversion, audit
    sla/        engine.py (pure deadline/breach math) + repository.py (live DB policy lookup)
    rca/        rule engine + LLM explainer + ai_validator.py (Priority 5)
    ingestion/  scraping + log ingestion + incident detection + recovery detection
    conversion/ legacy CSV converter
database/migrations/  raw SQL schema mirror + README on why there's no Alembic
prometheus/            scrape config
grafana/                provisioning + dashboard JSON
frontend/               dashboard (HTML/CSS/JS) - now with login + pagination
tests/
  unit/         integration/       load/       postman_collection.json
.github/workflows/ci.yml   unit + integration (real Postgres) + Docker build
data/legacy_csv/       sample dirty CSV used for the conversion feature
docs/                   all documentation listed above
```

## Honest note on what was verified where

Every piece of business logic, the database schema, and the full API
were run and tested for real (SQLite/Postgres, live FastAPI TestClient
processes, live pytest runs) during this upgrade - see
`FINAL_IMPLEMENTATION_AUDIT.md` for the exact PASS / FAIL / NOT RUN
status of every check, including the honest caveats about what could
not be exercised inside this sandboxed build environment (no Docker
daemon, no live Gemini key, no GitHub Actions runner).
