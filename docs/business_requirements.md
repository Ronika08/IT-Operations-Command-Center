# Problem Statement & Business Requirements

## Problem Statement

IT operations teams run many interdependent services. When something
breaks, three things need to happen fast and reliably: (1) detect the
failure from real signals, not from a customer complaint, (2) classify
and track it against an SLA so nothing silently blows past its deadline,
and (3) figure out the root cause quickly enough to fix it before the SLA
is breached. Manual dashboards and ad-hoc log-grepping don't scale past a
handful of services, and pure ML-based anomaly detection is often a
black box that's hard to defend to a manager or an auditor.

This project builds a small but real command center that does all three
end-to-end, against actual running services and actual Prometheus
metrics - not a mocked demo.

## Stakeholders / Target Users

- **On-call IT support engineer** - needs to see what's broken right now,
  acknowledge it, and get a starting point for diagnosis.
- **IT operations manager** - needs SLA compliance numbers and an audit
  trail, not a wall of raw logs.
- **New team member / auditor** - needs to be able to look at
  `audit_logs` and reconstruct exactly what happened and who/what did it.

## Functional Requirements

1. The system shall monitor real health and metrics endpoints for each
   registered service on a configurable interval.
2. The system shall automatically open an incident when a metric crosses
   a configurable threshold (error rate, latency, availability).
3. The system shall assign a severity and calculate response/resolution
   SLA deadlines from the moment an incident opens, **using a live,
   database-editable policy** (not a value baked into application code -
   see `docs/architecture.md`'s SLA section for why this distinction
   matters and was previously not true).
4. The system shall let an authenticated, authorized operator acknowledge
   and resolve an incident, recording the real acting user's identity and
   when it happened.
5. The system shall flag an incident as SLA-breached automatically when
   its deadline passes without the corresponding action.
6. The system shall ingest real, structured application log events from
   each monitored service (not just a single system-generated line per
   incident) and make them available to root-cause analysis.
7. The system shall automatically detect when a failed service's health
   returns to normal, recording that as a distinct, system-attributed
   fact - separate from a human declaring the incident resolved.
8. The system shall run a rule-based root-cause analysis against an
   incident's correlated metrics and logs, and honestly report
   "undetermined" when no rule matches confidently.
9. The system shall use an LLM to turn the rule engine's structured
   output into a plain-language explanation, grounded only in that
   evidence, and shall run a deterministic, explainable check on that
   explanation for obviously-unsupported claims before a human reviews it.
10. The system shall require an authenticated user with the appropriate
    role to acknowledge, resolve, or verify an RCA recommendation, and
    shall reject the action otherwise.
11. The system shall ingest a legacy CSV of historical incidents,
    validating, deduplicating, and logging the outcome of every row.
12. The system shall expose a dashboard showing current service health,
    open incidents (paginated), SLA compliance, recovery state, and AI
    validation status, gated behind a real login.

## Non-Functional Requirements

- **Configurability:** thresholds, service URLs, DB connection, JWT
  secret, and demo credentials must come from environment variables, not
  hard-coded values.
- **Explainability:** every RCA conclusion must trace back to specific
  evidence rows a human can inspect.
- **Auditability:** every state-changing action must produce an
  `audit_logs` row, attributed to a real user for every human-initiated
  action (a `NULL` user id is reserved for genuinely system-initiated
  events - see `docs/architecture.md`).
- **Resilience:** one service being down must not crash the collector's
  poll loop for the others; an unreachable or outdated service's `/logs`
  endpoint must not block ingestion for the rest.
- **Testability:** business logic (SLA calc, RCA rules, CSV validation,
  password hashing, JWT issuance, the AI validator, recovery detection)
  must be usable and testable without a running DB or network.
- **Least surprise:** a database table that appears to hold live
  configuration (like `sla_rules`) must actually be read by the code
  path it looks like it configures - not a display-only mirror of a
  hard-coded value. (This was violated before this upgrade; see
  `CHANGELOG_IMPROVEMENTS.md`.)

## Out of Scope (explicit, matches the project's scope discipline)

- Kubernetes orchestration.
- An ELK-style log-aggregation stack (each service's own small,
  bounded, in-memory log buffer + a pull-based `/logs` endpoint instead
  - see `docs/architecture.md`).
- ML-trained root cause classification (rule-based + LLM-explained only).
- A full ETL pipeline (one CSV conversion feature only).
- More than 4 backend services.
- A general-purpose permission system (4 fixed roles only - see
  `docs/security.md`).
- Enterprise IAM / SSO / a real identity provider (a real, but small,
  JWT + hashed-password login only).
- Alembic or another versioned migration runner (see
  `database/migrations/README.md` for the honest reasoning).
