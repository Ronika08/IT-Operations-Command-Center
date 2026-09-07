# Failure Injection Playbook

Every backend service exposes real `/admin/*` endpoints that change
actual runtime behaviour (not fake metric writes). Prometheus/the
collector then observe the real, resulting effect, and each fault
transition now also writes a real structured log line (see
`docs/architecture.md`, Priority 2) that RCA can correlate against.

**Two separate auth mechanisms - don't confuse them:**
- `/admin/*` on the 4 backend services (below) uses the `X-Admin-Token`
  header, matching each service's `FAULT_INJECTION_TOKEN` env var - this
  gates *fault injection*, not the dashboard.
- The Central Platform's API (`/incidents`, `/sla`, `/audit-logs`, etc.)
  now requires a real JWT from `POST /auth/login` (see
  `docs/security.md`). Every `curl` example against the central platform
  below assumes you've logged in first and have a token in `$TOKEN`.

## Common to all 4 services

| Endpoint | Effect | Log line produced |
|---|---|---|
| `POST /admin/inject-latency?ms=N` | Every request sleeps N ms before responding | WARNING "High latency injected" |
| `POST /admin/inject-errors?rate=0.0-1.0` | That fraction of requests return HTTP 500 | WARNING "Error injection enabled"; ERROR per actually-failed request |
| `POST /admin/simulate-down?down=true` | Every request (including `/health`) returns 503 | ERROR "Service simulated DOWN" |
| `POST /admin/reset` | Clears all injected faults | INFO "All faults reset" |

## Service-specific

| Service | Endpoint | Effect | Log line produced |
|---|---|---|---|
| payments-service | `POST /admin/simulate-db-timeout?on=true` | `/payments` calls sleep 2s then return 504, simulating a downstream DB dependency timing out | WARNING on toggle-on; ERROR **"Payment database request exceeded timeout"** per actual timed-out request - this is the exact evidence `rca/rules.py::_rule_downstream_db_timeout` looks for |
| db-proxy-service | `POST /admin/simulate-connection-exhaustion?on=true` | `/records` POSTs immediately return 503 with a "connection pool exhausted" message, and increment `db_connection_errors_total` | WARNING **"Connection pool exhausted - request rejected"** per rejected request - the exact evidence `_rule_connection_pool` looks for |

## Drill scripts actually run during this build

See `docs/rca_reports.md` for the full narrative, including Drill 2's
original honest miss (real logs were too thin to reach the specific
`downstream_dependency_failure` category) and Drill 4, added in this
upgrade, proving the fix.

## Recommended demo sequence for the interview (updated for this upgrade)

1. `docker compose up` - show all containers healthy, Grafana dashboard
   live with real request-rate/latency panels.
2. Sign in to the dashboard as `it_support`. Point out that every other
   tab was unreachable before login - this is a real JWT-gated API, not
   a cosmetic form.
3. **(New) Show the SLA policy is real, not decorative:** as `admin`,
   open the "SLA Policy" panel, change CRITICAL's response window (e.g.
   15 -> 2 minutes), hit Save. Point out the new audit-log row
   (`sla_rule_updated`) that appears immediately.
4. Kill a container: `docker stop auth-service` - within one poll
   interval, `/incidents` shows a new CRITICAL row **whose deadline
   reflects the value you just changed in step 3**, and Grafana's
   "Service Up/Down" panel flips. (Do this step *after* step 3, in this
   order, to actually see the new policy take effect on a fresh
   incident - an incident already open before the SLA change keeps its
   original deadline, correctly.)
5. `docker start auth-service`. Within one poll interval, the incident's
   status flips to `RECOVERED` (not `RESOLVED`) with a real timestamp -
   point out this happened with **no button click**, and that a human
   (`incident_manager` or `admin`) still has to explicitly resolve it.
6. Inject a DB timeout on payments-service
   (`POST /admin/simulate-db-timeout?on=true`), make a request to
   `/payments` so a real log line is produced, run RCA once ingestion has
   pulled that log in, and walk through the rule-based evidence
   (`downstream_dependency_failure`, high confidence) vs. the LLM's
   plain-language explanation vs. its `ai_validation_status` (should be
   `PASSED`) vs. the still-required human verification step (sign in as
   `rca_reviewer` to accept/reject/modify it).
7. Upload `data/legacy_csv/sample_incidents.csv` via the conversion
   endpoint as `admin` (now role-gated) and show the accept/reject/dedup
   counts and the resulting audit row.
