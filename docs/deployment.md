# Deployment Instructions

## Prerequisites

- Docker + Docker Compose v2
- (Optional) a Google Gemini API key, if you want live LLM-generated RCA
  explanations rather than the rule-based-only fallback

## 1. Configure environment

```bash
cp .env.example .env
# edit .env: set GEMINI_API_KEY if you want live LLM explanations, a
# real JWT_SECRET_KEY, and the 4 SEED_*_PASSWORD values (all default to
# obviously-fake placeholders otherwise - fine for a personal demo,
# not for anything shared - see docs/security.md)
```

**Upgrading an existing instance?** See
`database/migrations/README.md` first - `docker compose up` alone will
not retroactively add this upgrade's new columns/enum values to an
already-existing database. For a fresh install (no data to keep), just
`docker compose down -v` first.

## 2. Start everything

```bash
docker compose up --build
```

This builds and starts, in order:
1. `postgres` (waits for healthcheck before anything else starts)
2. `auth-service`, `orders-service`, `payments-service`, `db-proxy-service`
3. `central-platform` (runs DB init + seeds services/SLA rules on startup,
   then starts its background poll loop)
4. `prometheus` (scrapes all 4 services every 10s)
5. `grafana` (auto-provisioned with the Prometheus datasource and the
   "IT Operations Command Center - Service Health" dashboard)
6. `frontend` (static dashboard served by nginx)

## 3. Sign in

The dashboard now requires a real login (Priority 4). 4 demo accounts
are seeded on first startup, one per role - username equals role name:

| Username | Role | Password env var |
|---|---|---|
| `admin` | ADMIN | `SEED_ADMIN_PASSWORD` (default `changeme-admin`) |
| `it_support` | IT_SUPPORT | `SEED_IT_SUPPORT_PASSWORD` (default `changeme-itsupport`) |
| `incident_manager` | INCIDENT_MANAGER | `SEED_INCIDENT_MANAGER_PASSWORD` (default `changeme-incidentmgr`) |
| `rca_reviewer` | RCA_REVIEWER | `SEED_RCA_REVIEWER_PASSWORD` (default `changeme-rcareviewer`) |

See `docs/security.md` for what each role can actually do.

## 4. Access points

| Component | URL |
|---|---|
| Frontend dashboard | http://localhost:8080 |
| Central Platform API (Swagger docs) | http://localhost:8000/docs |
| Auth service | http://localhost:8001 |
| Orders service | http://localhost:8002 |
| Payments service | http://localhost:8003 |
| DB-proxy service | http://localhost:8004 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin/admin, or anonymous view enabled) |

## 5. Run the failure-injection drills

```bash
# /admin/* endpoints require the shared FAULT_INJECTION_TOKEN (see
# .env.example) in an X-Admin-Token header - added during the security
# audit, since these endpoints can force a service down.
ADMIN_TOKEN="dev-admin-token-change-me"

# First, log in to the central platform (separate from the token above -
# see the "two separate auth mechanisms" note in failure_injection.md)
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"it_support","password":"changeme-itsupport"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# Force payments-service into a bad state
curl -X POST "http://localhost:8003/admin/inject-errors?rate=1.0" \
  -H "X-Admin-Token: $ADMIN_TOKEN"

# Generate traffic so the fault is observable (and produces real logs)
for i in 1 2 3 4 5; do curl -X POST http://localhost:8003/payments \
  -H "Content-Type: application/json" -d '{"order_id":1,"amount":100}'; done

# Wait for the next poll cycle (POLL_INTERVAL_SECONDS, default 15s), or
# trigger immediately:
curl -X POST http://localhost:8000/services/3/poll-now -H "Authorization: Bearer $TOKEN"

# Check the incident opened (now paginated)
curl "http://localhost:8000/incidents?page=1&limit=20" -H "Authorization: Bearer $TOKEN"

# Run RCA on it
curl -X POST http://localhost:8000/incidents/1/rca -H "Authorization: Bearer $TOKEN"

# Reset the fault
curl -X POST http://localhost:8003/admin/reset -H "X-Admin-Token: $ADMIN_TOKEN"
```

## 6. Run the test suite

```bash
# Unit tests (no DB needed)
cd central-platform && pip install -r requirements.txt
pytest ../tests/unit/ -v

# Integration tests (needs a running Postgres - docker compose up postgres)
createdb itopscc_test  # or via psql
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/itopscc_test \
pytest ../tests/integration/ -v

# Load test
pip install locust
locust -f ../tests/load/locustfile.py --host http://localhost:8002 \
  --users 20 --spawn-rate 5 --run-time 15s --headless \
  --csv ../tests/load/results/orders_load
```

## 7. Convert legacy incident data

```bash
curl -X POST http://localhost:8000/conversion/legacy-incidents \
  -F "file=@data/legacy_csv/sample_incidents.csv"
```

## 8. Tear down

```bash
docker compose down -v   # -v also removes the Postgres volume
```

## Environment variables reference

See `.env.example` for the full list. Key ones:

| Variable | Default | Purpose |
|---|---|---|
| `ERROR_RATE_THRESHOLD` | 0.10 | Error-rate fraction that triggers a HIGH incident |
| `LATENCY_P95_THRESHOLD_SECONDS` | 1.0 | p95 latency that triggers a MEDIUM incident |
| `AVAILABILITY_THRESHOLD` | 0.99 | Availability below this triggers a CRITICAL incident directly (fixed during the audit - previously this env var was documented but ignored; the detector had a hard-coded `0.5` instead, see AUDIT_REPORT.md) |
| `POLL_INTERVAL_SECONDS` | 15 | How often the collector polls each service |
| `GEMINI_API_KEY` | (empty) | If unset, RCA falls back to rule-summary-only, gracefully. (This used to say `ANTHROPIC_API_KEY` here and in code - fixed this upgrade, see CHANGELOG_IMPROVEMENTS.md; the implementation has always actually called Gemini.) |
| `LLM_ENABLED` | true | Set to `false` to disable the LLM explanation layer entirely (also what CI uses) |
| `FAULT_INJECTION_TOKEN` | `dev-admin-token-change-me` | Shared secret required (as `X-Admin-Token` header) to call any `/admin/*` fault-injection endpoint on the 4 backend services |
| `JWT_SECRET_KEY` | `dev-only-jwt-secret-change-me` | Signs login tokens - MUST be overridden with a real random value outside a personal local demo |
| `JWT_EXPIRE_MINUTES` | 480 | How long a login session lasts |
| `SEED_ADMIN_PASSWORD` / `SEED_IT_SUPPORT_PASSWORD` / `SEED_INCIDENT_MANAGER_PASSWORD` / `SEED_RCA_REVIEWER_PASSWORD` | `changeme-*` | Demo login passwords, one per seeded role - see docs/security.md |
