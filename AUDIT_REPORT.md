# AUDIT_REPORT.md

**Audit scope:** full source inspection of `IT_Operations_Command_Center.zip`
(central platform, 4 backend services, DB schema, Docker/Prometheus/Grafana
config, tests, docs, frontend, Git history), performed against the
Applied Materials IT Solutions Management JD. This is a real audit: every
finding below was reached by reading the actual code and, where possible,
actually running it (28 unit tests → 55 after fixes, 8 integration tests
against a live local PostgreSQL 16 instance, all 4 microservices started
and hit with real HTTP requests). Docker/Compose/Prometheus/Grafana could
not be started in this sandbox (no Docker daemon) - their configs were
statically reviewed only, and that distinction is preserved throughout
this report rather than blurred.

---

## 1. Executive Summary

This project is **substantially stronger than the median "AI portfolio
project"** submitted for a role like this. It is not a shell of
scaffolding with fabricated screenshots - it runs, its tests genuinely
pass, its RCA engine has real fallback/honesty behavior instead of
always producing a confident answer, and its own documentation (bug
reports, RCA drill reports) is written with more intellectual honesty
than most production postmortems ("this is a scope boundary, not a
bug"; "the rule engine did the right thing with the evidence it had").

That said, an audit's job is to find what's wrong, not to admire what's
right, and a real audit found real problems:

- **One functional bug:** `AVAILABILITY_THRESHOLD` was documented as
  configurable but silently hard-coded (BUG-004).
- **One real security gap:** every fault-injection admin endpoint on all
  4 services was completely unauthenticated (BUG-005) - a genuine
  production-grade finding, not a nitpick.
- **One API-design inconsistency:** `db-proxy-service`'s `POST /records`
  was the only write endpoint in the platform taking an unvalidated raw
  query string instead of a JSON body (BUG-006).
- **One stored-XSS vector** in the frontend dashboard, reachable through
  the legacy-CSV conversion endpoint (BUG-007).
- **An inflated, incorrect test-count claim** repeated in three places
  (README, test_plan.md, skill_matrix.md): "36 unit tests" when the
  actual unit-only count was 28 (36 was unit+integration combined). This
  matters specifically because this project is being prepared for an
  interview - a reviewer who runs `pytest tests/unit/` and gets 28 while
  the README says 36 will (correctly) distrust everything else in the
  README.
- **A genuine, disclosed scope gap:** the `users` table and `AuditLog`
  relationship to a real user exist in the schema but nothing in the
  system ever creates a `User` row or authenticates a caller against it
  - every audit-log entry's `user_id` is `None` unless the API caller
  manually supplies one. This is a real "feature exists on paper"
  finding (see Phase 4 below), not previously called out anywhere in
  the docs.

All four functional/security findings (BUG-004 through BUG-007) were
**fixed in this session**, each with a real regression test that was
actually executed (not just written), and are documented in
`docs/bug_reports.md` following the project's own established
reproduce → root cause → fix → regression format. The full suite -
55 unit tests + 8 integration tests - passes cleanly after the fixes:
**63/63**.

---

## 2. Current Architecture (as built, verified)

```
Frontend (vanilla JS, nginx)
        │ REST/JSON
        ▼
Central Platform (FastAPI)
  - incidents / services / sla / conversion routers
  - background poll loop (asyncio, every POLL_INTERVAL_SECONDS)
  - rule-based RCA engine (app/rca/rules.py)
  - LLM explainer (app/rca/llm_explainer.py, Anthropic API)
        │ scrape /health, /metrics       │ SQL (SQLAlchemy)
        ▼                                ▼
  4 backend services                 PostgreSQL (8 tables)
  (auth/orders/payments/db-proxy)
  each: /health, /metrics (Prometheus
  format), /admin/* fault injection
        │ scraped by
        ▼
  Prometheus  →  Grafana
```

This matches what `docs/architecture.md` describes, and matches what the
code actually does - the architecture doc is accurate, not aspirational.
The scope-boundary decisions (no Kubernetes, no ELK, rule-based not ML
RCA, 4 services not more) are reasonable for what this project needs to
demonstrate and are argued, not just asserted, in the docs.

**Real weaknesses found in the architecture, not style complaints:**

1. **The `users`/`AuditLog.user_id` relationship is decorative.** The
   schema supports real per-user attribution, but no endpoint requires
   or validates a `user_id` - `AckRequest.user_id` and friends are all
   `Optional[int]` with no check that the ID refers to a real row, and
   nothing ever populates the `users` table. In an interview, "walk me
   through your audit trail" would currently mean walking through a
   table where `user_id` is `NULL` on every row from this session. This
   is flagged honestly here rather than fixed, because building real
   authentication is a genuinely large addition (see P2 in
   IMPROVEMENT_PLAN.md), not a quick patch, and the spec's own
   discipline against "unnecessary enterprise complexity" argues against
   rushing a shallow login system just to fill the column.
2. **Single point of correlation, not failure:** the central platform is
   the only thing that talks to Postgres, all 4 services, and the LLM.
   This is a reasonable choice at this scale (explicitly discussed in
   `docs/architecture.md`'s scaling section) but is worth being able to
   name unprompted in an interview as the system's one real bottleneck/
   SPOF, since an interviewer is likely to ask "what's the single point
   of failure in your design?"
3. **The background poller and the admin `poll-now` endpoint used to
   diverge** (this was BUG-002, already found and fixed by the original
   author before this audit - see `docs/bug_reports.md`). This is a
   legitimate historical example of the same class of bug this audit's
   own BUG-004 is: two code paths that are supposed to do the same
   logical thing, quietly drifting apart. Worth naming as a pattern in
   an interview, not just a one-off.

---

## 3. Applied Materials JD Scorecard

| JD Responsibility | Feature | Implementation | Evidence | Strength | Gap | Required Improvement |
|---|---|---|---|---|---|---|
| Analyzes business requirements → functional/technical specs → designs solutions | Written requirements before code | `docs/business_requirements.md` states functional/non-functional requirements and an explicit out-of-scope list | Doc reviewed, matches implementation | High - genuinely traceable from requirement to code | Requirements doc predates the audit's own fixes (BUG-004–007) and wasn't updated with them until this session | Keep requirements docs living, not a one-time artifact (done this session for the 4 fixes) |
| Performs/documents configuration & coding; executes unit/integration/performance/acceptance testing | 4 services + central platform; 55 unit + 8 integration + Locust + Postman | Verified: ran the full test suite myself this session | `tests/`, this report §7 | High | Test-count claims were wrong in 3 docs (fixed this session) | Keep a single source of truth for test counts (a Makefile/CI badge, not hand-typed numbers) |
| Reviews/monitors production systems for continuous performance; determines modifications | Real Prometheus scrape config + threshold-based auto-detection; found/fixed BUG-004 (a monitoring threshold silently not configurable) | `app/ingestion/collector.py`, this audit's BUG-004 | Strong - this audit finding is itself evidence of "reviewing a production system and determining a needed modification" | Prometheus/Grafana never actually started in this sandbox - config is statically correct, not live-verified here | Verify live once Docker is available (see FINAL_AUDIT_REPORT.md "what I must not claim") |
| Provides IT support w/ supervision; adheres to SLA process; debugs root causes | SLA engine (boundary-tested this session), RCA rule engine + LLM explainer with honest "undetermined" fallback | `app/sla/engine.py`, `app/rca/`, `docs/rca_reports.md`, `docs/bug_reports.md` | Very strong - the "undetermined" fallback and BUG-003's honest write-up are the single best pieces of evidence in the whole project for hallucination-awareness and engineering honesty | Admin fault-injection endpoints (the mechanism used to *produce* the incidents being debugged) had no auth (BUG-005, fixed) | Keep the fix; extend real user auth in a later pass (P2) |

**Scores (1–10, brutally honest):**

| Category | Score | Why |
|---|---|---|
| Technical depth | 8 | Real layered architecture, real DB, real metrics parsing (hand-rolled Prometheus exposition parser is a genuinely above-median touch), rule-based RCA with actual evidence weighting |
| IT Operations relevance | 8 | Incident/SLA/RCA/failure-injection loop maps directly onto the JD's language, not generic CRUD |
| Production/monitoring | 6 | Real scrape config and threshold logic, but never run end-to-end against live Prometheus/Grafana in *any* session including this one - that gap is disclosed, not papered over |
| Testing | 8 (was 6 before this session - +27 tests, +1 file, boundary cases added) | Real integration tests against real Postgres, not mocks; still no headless-browser test for the frontend XSS fix |
| Debugging/RCA | 9 | The RCA "undetermined" fallback and honestly-written BUG-003/rca_reports.md drills are the strongest single asset in the project for this exact JD line item |
| Database | 7 | Clean normalized schema, real FKs/indexes/enums; `users`/audit attribution is schema-only (not wired to real auth) |
| Backend/API | 7 (was 6 - the `/records` fix removes the one real API-design inconsistency) | Consistent REST conventions, correct status codes, real Pydantic validation almost everywhere |
| System design | 7 | Scope discipline (no k8s/ELK/ML) is itself a system-design signal, argued rather than assumed |
| AI implementation | 9 | Grounding, refusal-to-guess, human-in-the-loop verification, graceful degradation without a key - this is a genuinely well-designed hallucination-mitigation pattern, not a wrapper around a chat call |
| Documentation | 7 (was 5 - fixed 3 files' false test-count claims, added 4 new bug reports) | Extensive and mostly accurate; the test-count error was a real integrity problem, now fixed |
| Interview defensibility | 8 | The honest "here's where the RCA engine got it wrong and why" write-ups are exactly what survives a skeptical technical interviewer; the fixed security gap is now also a strong "tell me about a bug you found" story |

**Overall (unweighted mean of the 11 category scores above): 76/100.**
Before this session's fixes it would have scored meaningfully lower on
Testing, Backend/API, and Documentation specifically because of the
security gap, the dead config variable, and the false test-count claim -
those are exactly the kind of thing a technical interviewer probes for
and would have cost real credibility if found live in an interview
rather than fixed beforehand.

---

## 4. "Stand Out" Analysis

**Classification: D → trending toward E** (Strong IT Operations/
Solutions Management project, with real interview-level pieces,
specifically the RCA honesty pattern and the bug-report discipline).

It is clearly **not** (A) a generic AI-portfolio shell - there is no
fabricated dashboard, no mocked metrics, no LLM standing in for logic
that should be deterministic. It is also above (B) "good student
project" - the SLA boundary-condition thinking, the rule-engine
weight-based confidence scoring, and especially the RCA drill write-ups
that admit when the system got the wrong (but defensible) answer are not
things a typical student project does.

What's keeping it from a clean (E) "interview-level, stands out" rating
outright:
1. It had a real, if disclosed, false-claim problem (test counts) - the
   kind of thing that costs credibility fast in a technical interview if
   caught live instead of pre-empted.
2. It had a real security gap (unauthenticated admin endpoints) that a
   security-aware interviewer would likely probe for directly ("what
   happens if I call this endpoint with no auth?").
3. Nothing in the stack (Docker/Prometheus/Grafana/full end-to-end
   demo) has ever actually been run in one continuous live session by
   anyone, including this audit - it is real code that has never been
   proven to work together as one running system. That's the single
   biggest remaining risk to "interview-defensible."

**Top 10 changes with the greatest interview value (in priority order):**

1. Fix the unauthenticated `/admin/*` endpoints - **done this session** (BUG-005).
2. Fix the dead `AVAILABILITY_THRESHOLD` - **done this session** (BUG-004).
3. Fix the false test-count claims - **done this session**.
4. Fix the `/records` API-design inconsistency - **done this session** (BUG-006).
5. Fix the frontend stored-XSS - **done this session** (BUG-007).
6. Add the SLA boundary-condition tests (exact-deadline, one-second-late,
   already-resolved-stays-resolved) - **done this session**.
7. **Actually run `docker compose up` once, end to end, on a machine
   with Docker, and capture the real output** - this is the single
   highest-value remaining action and this audit could not do it (no
   Docker daemon in this sandbox). See IMPROVEMENT_PLAN.md P0-1.
8. Wire a minimal real login (even a single hard-coded admin/operator
   pair checked against the `users` table with a hashed password) so
   `AuditLog.user_id` stops being always-`NULL` - P2, real but not
   urgent.
9. Add a headless-browser regression test for the XSS fix (Playwright or
   similar) - P2.
10. Migrate `datetime.utcnow()` to timezone-aware `datetime.now(UTC)`
    throughout - real Python 3.12 deprecation debt, but touches DB
    comparison code in a way that needs careful testing against the
    `TIMESTAMP` (not `TIMESTAMPTZ`) column type; scoped as P2/P3, not
    rushed in this session (see IMPROVEMENT_PLAN.md for why).

---

## 5. Real vs Fake Functionality Audit (Phase 4)

| Feature | Classification | Basis |
|---|---|---|
| Service health checks (`/health`) | **REAL AND VERIFIED** | Started auth-service and db-proxy-service live this session; hit `/health`, got real 200/503 responses that change with fault-injection state |
| Prometheus metrics (`/metrics`) | **REAL AND VERIFIED** | Live-curled `/metrics` from a running auth-service; real `prometheus_client`-generated exposition text |
| Prometheus server itself scraping these | **REAL BUT NOT VERIFIED (this session)** | Config is correct and consistent with what services emit; no Docker daemon available to actually start the Prometheus container here |
| Grafana dashboard | **REAL BUT NOT VERIFIED (this session)** | Dashboard JSON is valid and its PromQL references real metric names; never rendered live in any session on record |
| Incident auto-detection (threshold breach → Incident row) | **REAL AND VERIFIED** | Exercised via `tests/unit/test_incident_detection.py` (in-memory DB) and the pre-existing integration tests against live Postgres |
| SLA deadline calculation & breach detection | **REAL AND VERIFIED** | 14 unit tests including new boundary cases (exact-deadline, one-second-late) all pass |
| Rule-based RCA | **REAL AND VERIFIED** | 7 unit tests including the deliberately-ambiguous "undetermined" case; also exercised via a live integration test |
| LLM RCA explanation | **REAL, gracefully degrades, NOT LIVE-VERIFIED (no API key in this session)** | Code correctly falls back to a rule-summary-only string when `ANTHROPIC_API_KEY` is unset - this fallback path *was* exercised live (integration test asserts `used_llm is False`); the actual Anthropic API call path was not exercised in this session |
| Database schema / persistence | **REAL AND VERIFIED** | Ran the raw migration-equivalent (`Base.metadata.create_all`) against a real local PostgreSQL 16 instance; full integration suite passed against it |
| CSV legacy-data conversion | **REAL AND VERIFIED** | 7 unit tests + 1 integration test (upload → accept/reject/dedup → real DB insert) all pass |
| Docker / docker-compose | **DOCUMENTATION ONLY (this session) / STATICALLY REVIEWED** | No Docker daemon available in this sandbox; `docker-compose.yml` was read and reasoned about but never executed here. This is explicitly the single biggest unverified claim in the project |
| Frontend dashboard | **REAL, security-fixed, NOT VISUALLY VERIFIED** | Code reviewed and one real vulnerability (stored XSS) found and fixed; never rendered in an actual browser in this session (no browser tooling available) |
| Failure-injection admin endpoints | **REAL AND VERIFIED, now also SECURED** | Live-curled auth-service and db-proxy-service's `/admin/*` endpoints this session; confirmed real behavior change (500s, 401s) |
| Postman collection | **REAL, statically valid, NOT RUN (this session)** | JSON re-validated after edits; `newman` was not invoked in this sandbox |
| Locust load test results | **REAL PAST RUN (not reproduced this session)** | The checked-in CSVs have the shape of genuine Locust output; not re-run here since orders-service wasn't kept running long enough to load-test in this pass |
| `users` table / real authentication | **PARTIALLY IMPLEMENTED / SCHEMA ONLY** | Table exists, `AuditLog.user_id` FK exists, but nothing in the system ever creates a real user or checks a real credential against it for audit purposes - a genuine gap, disclosed here for the first time |

---

## 6. Database Audit (Phase 6)

Schema is clean: every FK is present and indexed, enums are enforced at
the DB level (not just app-level `if` checks), and `sla_rules` has a
real `UNIQUE` constraint on `severity` preventing duplicate policies.
`incidents`, `logs`, and `metrics` are indexed on both `service_id` and
their timestamp column, which is exactly what the RCA engine's
time-windowed queries need.

**Real gaps found:**
- No index on `recommendations.created_at` or a composite
  `(incident_id, created_at)` index - not a problem at this data volume,
  but worth naming if asked "how would this schema need to change to
  scale."
- `AuditLog.target_id` is a bare `Integer`, not a real FK to anything
  (correctly, since it's polymorphic across incidents/recommendations) -
  this is a reasonable tradeoff (documented in `docs/architecture.md`),
  but it does mean the DB itself cannot enforce that `target_id` points
  at a real row; worth naming as a deliberate tradeoff, not an oversight.
- As covered in §5, `users.password_hash` exists but nothing ever writes
  to it - `auth-service`'s own `USERS_DB` dict is a completely separate,
  unrelated in-memory store from the central platform's `users` table.
  These are two disconnected "user" concepts in the same project, which
  is the kind of inconsistency an interviewer would catch by asking
  "so when I log into auth-service, does that create an audit-log
  entry anywhere?" (Answer, honestly: no, not currently.)

No SQL injection risk was found - every query goes through SQLAlchemy's
ORM/parameter binding, and the one raw-SQL surface
(`db-proxy-service`'s `sqlite3.execute(..., (payload,))`) already used
parameterized placeholders correctly even before this audit's `/records`
fix (that fix was about input *validation*, not injection - important
to say precisely rather than overstate the finding).

---

## 7. Testing Audit (Phase 13) - actual results, this session

```
$ pytest tests/unit/ -v         →  55 passed
$ pytest tests/integration/ -v  →  8 passed   (against a real local PostgreSQL 16)
```
**Total: 63/63 passed.** These commands were actually run in this
sandbox; nothing here is a predicted or claimed result.

What was **not** re-run in this session: Postman/`newman` (JSON
validity was re-checked instead), and the Locust load test (results on
disk are from a prior real run, not regenerated now). Both are called
out explicitly rather than silently reused as if fresh.

The pre-existing test quality is genuinely good - not
`assert response.status_code == 200`-style filler. The ambiguous-RCA
test, the double-acknowledge-returns-400 test, and the
unknown-service-skipped-during-CSV-import test are all testing real
business rules, not framework plumbing.

---

## 8. Security Audit (Phase 16)

| Area | Finding | Status |
|---|---|---|
| Admin fault-injection endpoints | No auth at all | **Fixed this session** (BUG-005) |
| `db-proxy /records` input validation | Unbounded, unvalidated raw string | **Fixed this session** (BUG-006) |
| Frontend stored XSS | User-controlled CSV data rendered via unescaped `innerHTML` | **Fixed this session** (BUG-007) |
| SQL injection | None found - all queries parameterized (ORM or explicit placeholders) | No action needed |
| Secrets in repo | `.env.example` correctly contains no real secrets; `.gitignore` excludes `.env` | No action needed |
| `auth-service` password storage | Plaintext dict (`USERS_DB`), and disconnected from the real `users.password_hash` column entirely | **Not fixed - flagged as P2.** This is a demo auth surface, not a real login system; fixing it properly means building the real login flow described in §6, which is a bigger, deliberate addition rather than a quick patch |
| CORS | `CORS_ORIGINS=*` by default | Acceptable for local/demo use, explicitly called out in `docs/deployment.md` as something to restrict in any non-local deployment - already documented, no change needed |
| Fake JWT (`fake-jwt-for-{username}`) | Not a real token, clearly labeled as such in the response | Acceptable for a demo auth service **as long as this is stated plainly in an interview** - see FINAL_AUDIT_REPORT.md "what I must not claim" |

---

## 9. Documentation Audit (Phase 17)

Documentation coverage against the requested list is essentially
complete: problem statement, functional/non-functional requirements,
architecture + ER diagram (described, not a rendered image - worth
noting precisely), API reference, test plan with real results, bug
reports, RCA reports, failure-injection playbook, deployment guide, and
a JD skill-matrix mapping all exist and were, before this audit, mostly
accurate.

**The one real documentation integrity problem found:** the test-count
claim ("36 unit tests") was wrong in 3 files (README.md,
docs/test_plan.md, docs/skill_matrix.md) - all three now corrected to
the real number (55, post-audit) with an explicit note about the
original discrepancy rather than silently changing the number. This
matters more than a typo would: a candidate citing a specific number in
an interview and being wrong about it, when the real command to check it
takes 5 seconds to run, is a credibility risk disproportionate to the
size of the error.

No other fabricated claims, invented screenshots, or unexecuted test
results were found in the documentation set.
