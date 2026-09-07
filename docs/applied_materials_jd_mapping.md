# Applied Materials JD Mapping

For each responsibility: **WHAT I BUILT**, **HOW IT WORKS**, **WHAT
EVIDENCE EXISTS**, **WHAT IS STILL A LIMITATION**. No responsibility
below is exaggerated past what the code and tests actually support -
where the match is weak or indirect, that's stated plainly.

---

### 1. Business requirements -> functional/technical specifications -> solution design

**WHAT I BUILT:** A written requirements document
(`docs/business_requirements.md`) with explicit functional/non-
functional requirements and an explicit out-of-scope list, followed by
an architecture document (`docs/architecture.md`) that traces every
requirement to a real component.

**HOW IT WORKS:** Requirements were written before/alongside the code,
then re-verified against the actual implementation via an independent
audit (`FINAL_IMPLEMENTATION_AUDIT.md`) that checked each requirement
against real code paths rather than trusting the docs.

**EVIDENCE:** `docs/business_requirements.md`, `docs/architecture.md`,
and the audit's finding that requirement #3 ("SLA deadlines... using a
live, database-editable policy") was *written* correctly but not
initially *implemented* correctly - and the subsequent fix.

**LIMITATION:** This is a solo project - there was no real stakeholder
negotiation, competing-priorities tradeoff, or sign-off process, all of
which are a real part of this JD responsibility in an enterprise
setting.

---

### 2. Software configuration and coding (documented)

**WHAT I BUILT:** All 5 services' code, with environment-variable-driven
configuration throughout (thresholds, DB connection, JWT secret, seed
passwords, LLM provider settings) - nothing environment-specific is
hard-coded.

**HOW IT WORKS:** `app/core/config.py` centralizes central-platform
configuration; each service reads its own env vars directly. Code
changes are documented in `CHANGELOG_IMPROVEMENTS.md` with before/after,
files changed, and reasoning for each change - not just a commit
message.

**EVIDENCE:** `.env.example`, `docker-compose.yml`, git history (15
original commits + this upgrade's commits), `CHANGELOG_IMPROVEMENTS.md`.

**LIMITATION:** No formal code review process (no second engineer) -
the "review" here is the self-audit process, which is a real practice
but not a substitute for another person's eyes.

---

### 3. Unit / integration / performance / acceptance testing + data conversion

**WHAT I BUILT:** 89 unit tests, 24 integration tests, a real Locust
load-test run with results checked in, a Postman collection, and a real
CSV-to-database data-conversion feature (validate -> dedup -> insert).

**HOW IT WORKS:** See `docs/testing.md` for the full breakdown,
including exactly what was and wasn't re-executed during this specific
upgrade (with honest sandbox-environment caveats).

**EVIDENCE:** `tests/unit/`, `tests/integration/`, `tests/load/results/`,
`tests/postman_collection.json`, `central-platform/app/conversion/csv_converter.py`
+ its own tests.

**LIMITATION:** The load test predates this upgrade and doesn't exercise
the newly auth-gated endpoints yet; acceptance testing here means
scripted demo scenarios (`docs/failure_injection.md`,
`docs/rca_reports.md`), not formal UAT sign-off from a business
stakeholder.

---

### 4. Reviews/monitors production systems, determines modifications

**WHAT I BUILT:** Real threshold-based monitoring (not simulated), plus
the self-audit process itself, which is a form of "reviewing a running
system and determining what needs to change."

**HOW IT WORKS:** `app/ingestion/collector.py` polls real health/metrics/
logs on a schedule; the audit that produced this upgrade is a second,
independent instance of exactly this JD line - reviewing the actual
running (or buildable) system and identifying concrete, evidenced
modifications, not assumptions.

**EVIDENCE:** `FINAL_IMPLEMENTATION_AUDIT.md`, `CHANGELOG_IMPROVEMENTS.md`,
`docs/bug_reports.md` (8 documented findings across two audit rounds).

**LIMITATION:** This is a demo system with synthetic failure modes I
control, not a real production system under genuine, unpredictable
load - the *practice* of systematic review transfers; the specific
failure patterns don't necessarily.

---

### 5. IT service support, service management processes

**WHAT I BUILT:** A real incident lifecycle (open -> acknowledge ->
[recovered] -> resolve) with role-gated actions and a full audit trail.

**HOW IT WORKS:** `routers/incidents.py`, backed by
`ACTIVE_INCIDENT_STATUSES` and the recovery/resolution distinction in
`app/models/db.py`.

**EVIDENCE:** `tests/integration/test_api.py::test_incident_lifecycle_ack_and_resolve`
and the recovery-specific tests in `tests/unit/test_recovery_detection.py`.

**LIMITATION:** No ticketing-system integration, no on-call/paging
escalation, no SLA-driven auto-escalation - the lifecycle is complete but
self-contained, not integrated into a broader service-management
toolchain (ServiceNow, PagerDuty, etc.).

---

### 6. Manages SLA and customer satisfaction

**WHAT I BUILT:** Real, boundary-tested SLA breach detection, now backed
by a genuinely live, database-editable policy (the Priority 1 fix - see
`docs/bug_reports.md` BUG-008 for the honest story of what was wrong
before).

**HOW IT WORKS:** `app/sla/engine.py` (pure deadline/breach math) +
`app/sla/repository.py` (live policy lookup) + `PUT /sla/rules/{severity}`
(admin-controlled write path).

**EVIDENCE:** `tests/unit/test_sla_repository.py`,
`tests/integration/test_api.py::test_sla_rule_update_accepted_for_admin_and_changes_the_value`.

**LIMITATION:** "Customer satisfaction" isn't measured at all - there's
no CSAT/feedback mechanism, just SLA-adherence tracking. Worth naming
this gap directly if asked, rather than stretching SLA compliance to
stand in for satisfaction.

---

### 7. System analysis and debugging

**WHAT I BUILT:** A documented trail of 8 real, reproduced bugs across
two audit rounds, each with root cause, fix, and regression test.

**HOW IT WORKS:** `docs/bug_reports.md`.

**EVIDENCE:** Git history showing the actual fixes; regression tests
re-run and confirmed passing during this upgrade (see
`FINAL_IMPLEMENTATION_AUDIT.md`'s test-results table).

**LIMITATION:** All bugs were self-found in a system I built - there's
no experience here debugging an unfamiliar, large, legacy codebase
written by someone else, which is a different (and real) skill.

---

### 8. Identifies problem root causes

**WHAT I BUILT:** A rule-based RCA engine that returns specific,
evidenced categories or an honest "undetermined," plus an LLM
explanation layer and a deterministic validator on that explanation.

**HOW IT WORKS:** `app/rca/rules.py` (deterministic), `app/rca/llm_explainer.py`
(constrained explanation), `app/rca/ai_validator.py` (evidence-consistency
check) - see `docs/rca_reports.md` for real drills, including one honest
miss (Drill 2) and its fix (Drill 4, this upgrade).

**EVIDENCE:** `tests/unit/test_rca_rules.py`, `tests/unit/test_service_logs.py`
(proves real service-generated logs, not fixtures, drive the correct
category), `tests/unit/test_ai_validator.py`.

**LIMITATION:** Only 5 hand-written rules cover a handful of specific
failure signatures - a genuinely novel failure mode outside those 5
patterns will correctly fall through to "undetermined" rather than
being diagnosed, which is the intended, honest behavior, but is a real
scope boundary worth stating.
