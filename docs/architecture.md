# Architecture

## System Architecture (3-tier + monitoring plane)

```
                         ┌────────────────────────────┐
                         │        Frontend (JS)        │
                         │ Login + Dashboard + Detail   │
                         └──────────────┬───────────────┘
                                        │ REST (JSON) + JWT bearer token
                                        ▼
                         ┌────────────────────────────┐
                         │      Central Platform        │
                         │  FastAPI: auth, incidents,   │
                         │  sla, services, conversion,  │
                         │  audit routers                │
                         │  + background poll loop       │
                         │  (metrics + log ingestion +   │
                         │   recovery detection)          │
                         │  + RCA rule engine             │
                         │  + LLM explainer (Gemini)      │
                         │  + AI-explanation validator     │
                         └───┬───────────┬───────────┬──┘
                             │ scrape+    │ SQL        │ scrape+admin
                             │ /logs pull │            │
                             ▼            ▼            ▼
                 ┌────────────────┐ ┌──────────┐ ┌─────────────────────────┐
                 │  4 backend      │ │PostgreSQL│ │      Prometheus          │
                 │  services       │ │(8 tables)│ │  scrapes /metrics on all │
                 │  auth/orders/   │ └──────────┘ │  4 services every 10s -  │
                 │  payments/      │              │  visualization only, NOT │
                 │  db-proxy       │              │  in the detection path   │
                 │  each: /health, │              │  (see "What Prometheus   │
                 │  /metrics,      │              │  actually does" below)   │
                 │  /logs,         │              └─────────────┬────────────┘
                 │  /admin/* fault │                             │
                 │  injection      │                             ▼
                 └─────────────────┘                     ┌───────────────┐
                                                           │    Grafana     │
                                                           │ health dashboard│
                                                           └───────────────┘
```

## What Prometheus actually does (and doesn't)

This is worth stating precisely because it's easy to assume otherwise
from the diagram: **the Central Platform does not go through Prometheus
at all.** It scrapes each service's `/health`, `/metrics`, and `/logs`
directly itself, via plain HTTP, for its own detection/RCA purposes.
Prometheus runs a second, independent scrape of the same `/metrics`
endpoints purely so Grafana has something to draw. If Prometheus and
Grafana were both stopped, detection/incidents/RCA/SLA would keep
working exactly as before - only the Grafana panels would go stale.

## Data flow for one incident, end to end

1. A service's own request handling produces real Prometheus counters
   (`http_requests_total`, `http_request_duration_seconds`, etc.) **and**
   real structured log events (`app/applog.py` in each service - a login
   attempt, an order/payment outcome, a fault-injection state change, a
   DB-timeout or connection-pool-exhaustion error).
2. The Central Platform's poll loop, every `POLL_INTERVAL_SECONDS`:
   a. scrapes `/health` + `/metrics` from each service (`collector.py::poll_service`),
   b. pulls any new `/logs` entries via a durable per-service cursor (`collector.py::ingest_logs`),
   c. checks whether any currently-active incident for that service has
      recovered (`collector.py::check_recovery`),
   d. evaluates the real thresholds and opens a new `Incident` if breached.
3. **SLA policy fix (this upgrade):** when an incident opens, its
   `response_due_at`/`resolution_due_at` are calculated from the **live**
   `sla_rules` row for that severity (`app/sla/repository.py::get_sla_policy_for_severity`),
   not a hard-coded default. Changing a row via `PUT /sla/rules/{severity}`
   (ADMIN-only) changes the deadline of the next incident of that
   severity - this used to not be true (see `CHANGELOG_IMPROVEMENTS.md`).
4. An operator (or the RCA endpoint) reads back the `MetricSample` and
   `LogEntry` rows in the incident's time window, runs the rule engine,
   asks the LLM (Gemini) to explain the result, runs the deterministic
   `ai_validator.py` check against that explanation, and stores all of it
   as a `Recommendation`, unverified until an RCA_REVIEWER/ADMIN reviews it.
5. If a later poll observes the triggering condition no longer breaches,
   `check_recovery()` marks the incident `RECOVERED` (system fact,
   timestamped, audited) - distinct from `RESOLVED` (human decision).
6. Every lifecycle action (login, acknowledge, resolve, verify, SLA
   change, auto-recovery) writes a real `AuditLog` row, with the real
   authenticated user's id for every human-initiated action.

## Why this shape, not something bigger

- **No Kubernetes:** 4 services + a platform is well within what
  docker-compose can run and reason about; k8s would add operational
  complexity with no benefit at this scale, and would distract from the
  actual JD-relevant skills (monitoring, SLA, RCA, debugging).
- **No ELK/queue for logs:** each service's own real, bounded (500-entry)
  in-memory log buffer + a `/logs` pull is the smallest mechanism that
  gets real structured events into Postgres for RCA to use, without
  standing up log-aggregation infrastructure for what is, at this scale,
  4 processes' worth of events.
- **Rule-based RCA, not ML:** with 4 services and a handful of failure
  modes, there isn't enough labeled failure data to train a real
  classifier responsibly - and a rule engine is what's actually
  explainable to a manager, which is the point.
- **JWT, not a session store/enterprise IAM:** a stateless bearer token
  with 4 fixed roles is enough to demonstrate real authn/authz and real
  audit attribution without building (or half-building) an identity
  provider - see `docs/security.md`.

## Database ER Diagram (described)

```
users ──< audit_logs >── (target_type/target_id, not a hard FK - it can
                           point at incidents, recommendations, sla_rules, etc.)

services ──< incidents
services ──< logs
services ──< metrics

incidents ──< recommendations

sla_rules (standalone lookup table, one row per severity - now the real
           runtime source of truth for incident deadlines, see above)
```

Foreign keys: `incidents.service_id -> services.id`,
`logs.service_id -> services.id`, `metrics.service_id -> services.id`,
`recommendations.incident_id -> incidents.id`,
`audit_logs.user_id -> users.id` (nullable - see below for what NULL
means now).

**What a NULL `audit_logs.user_id` means, precisely (this changed in
this upgrade):** before real auth existed, every row had `user_id = NULL`
because there was no way to attribute an action to anyone. Now, every
*human-initiated* action (login-gated ack/resolve/verify/SLA-change/CSV-
import) always carries a real, authenticated user id - `NULL` is
reserved exclusively for the one genuinely system-initiated audit event,
`auto_recovery_detected`, which the monitoring poller writes with no
human involved. A NULL row after this upgrade is a meaningful signal
("the system did this"), not a gap.

See `database/migrations/001_initial_schema.sql` +
`database/migrations/002_operational_upgrades.sql` for the exact DDL,
and `database/migrations/README.md` for why there's no automated
migration runner applying them.

## Scaling from 100 to 100,000 users (reasoned, not memorized)

This system's actual load isn't "users" in the web-traffic sense - it's
operators viewing dashboards and services being polled. The honest
scaling conversation:

- **Read load (dashboard/API):** stateless FastAPI behind a load
  balancer, horizontal replicas - mostly fine, since the central
  platform holds no in-process state other than the poll-loop task. One
  concrete bottleneck found in this project's own audit: `GET /incidents`
  used to refresh and `commit()` breach flags once per row on every read
  - fixed this upgrade to one batched commit per page (see
  `CHANGELOG_IMPROVEMENTS.md`), but the underlying pattern (recomputing
  derived state on every read) is worth knowing the tradeoffs of.
- **Poll/ingestion load:** at 4 services this is a simple sequential
  loop; at thousands of services, this is where you'd introduce a queue
  or a sharded set of pollers instead of one process polling everything
  serially in-line with the API server - this is the point where the
  "no queue" scope decision would need revisiting, and I'd say so
  directly in an interview rather than pretend the current design scales
  as-is.
- **Multiple central-platform replicas:** the duplicate-open-incident
  guard is a DB read-then-write with no unique constraint backing it -
  fine for one poller process (today's design), but two replicas both
  running the background loop could theoretically race and double-open
  an incident. A real multi-replica deployment would need either a
  unique partial index (`WHERE status IN (...)`) or to move the poller
  out of the API process entirely into its own single worker.
- **Database:** PostgreSQL read replicas for dashboard queries, and
  partitioning `metrics`/`logs` by time once retention grows, since those
  are the two tables with unbounded growth.
- **RCA/LLM calls:** rate-limited and queued rather than synchronous,
  since LLM latency shouldn't block incident creation.
