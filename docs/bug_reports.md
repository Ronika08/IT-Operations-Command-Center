# Bug Reports

Real bugs found while building and testing this project, using the
reproduce -> evidence -> isolate -> root cause -> fix -> regression flow.
These are not staged - they are the actual defects hit during
implementation, kept here as evidence of the debugging process the JD
asks for (bullet 4: "performs system analysis and debugging to identify
root causes").

---

## BUG-001: `/conversion/legacy-incidents` crashes the app at import time

**Severity:** High (blocks app startup entirely)

**Reproduce:**
Run `pytest tests/integration/test_api.py`, which imports `app.main` (and
therefore `app.routers.conversion`, which uses FastAPI's `UploadFile`).

**Evidence:**
```
RuntimeError: Form data requires "python-multipart" to be installed.
```
Full traceback pointed at `fastapi/dependencies/utils.py:ensure_multipart_is_installed`,
triggered while FastAPI was building the route for `POST /conversion/legacy-incidents`.

**Isolate:**
FastAPI's `UploadFile` / multipart form parameters have an optional runtime
dependency (`python-multipart`) that FastAPI itself does not install
transitively. The route definition succeeds at the Python level but fails
the moment FastAPI introspects the endpoint's parameters to build its
OpenAPI/dependency graph - which happens at import time, not at request
time. So the failure mode is "the whole app won't start," not "this one
endpoint 500s."

**Root cause:**
`central-platform/requirements.txt` was missing `python-multipart`.

**Fix:**
Added `python-multipart==0.0.9` to `central-platform/requirements.txt`.

**Regression check:**
Re-ran `pytest tests/integration/test_api.py -v` - all 8 tests, including
`test_csv_conversion_endpoint_inserts_known_services_only`, passed after
the fix. Confirmed via `pip install python-multipart` + rerun in the same
session (see terminal log from that stage of the build).

---

## BUG-002: `POST /services/{id}/poll-now` never opened an incident

**Severity:** High (breaks the manual failure-injection-drill workflow;
the background poller was unaffected)

**Reproduce:**
1. Started the full stack (4 services + central-platform) locally.
2. Injected a 100% forced error rate into payments-service:
   `POST /admin/inject-errors?rate=1.0`.
3. Sent a few requests to `/payments` to generate real Prometheus counter
   activity.
4. Called `POST /services/3/poll-now` (payments-service's id) expecting an
   incident to open, since availability should read as 0.0 (health checks
   also fail under 100% forced errors).
5. `GET /incidents` returned `[]`.

**Evidence:**
`poll-now`'s response showed the real, correctly-computed metrics
(`"availability": 0.0`), proving the collector and threshold logic were
both fine - the incident just never got created.

**Isolate:**
Read `app/routers/services.py`: the `poll_now` handler called only
`poll_service(db, service)`, which stores `MetricSample` rows but never
calls `detect_and_open_incident`. That function is only invoked from the
background `poll_loop()` in `app/main.py`. So automatic (background)
detection worked; on-demand detection via the API did not.

**Root cause:**
An asymmetry between the background poller and the manual "poll now"
endpoint - the manual endpoint was originally written just to preview
metrics for the frontend/demo, and detection was added later only to the
background path.

**Fix:**
`poll_now` now also calls `detect_and_open_incident(db, service, metrics)`
and returns the opened incident (if any) in its response, matching what
the background loop does.

**Regression check:**
Re-ran the full drill (100% forced error rate on payments-service, then
`poll-now`): response now includes
`"incident_opened": {"id": 1, "title": "payments-service is unavailable", "severity": "critical"}`,
and `GET /incidents` shows the real row with correct SLA deadlines
(response due 15 min later, resolution due 4h later - matching the
CRITICAL policy in `sla_rules`).

---

## BUG-003 (design note, not a code bug): RCA log evidence is currently thin

**Observed while investigating a scenario:** injecting a DB-timeout-style
failure (`/admin/simulate-db-timeout`) into payments-service produced a
real incident, but running RCA on it classified the root cause as
`elevated_error_rate` rather than the more specific
`downstream_dependency_failure`, even though the failure was in fact a
simulated downstream timeout.

**Why:** the only `LogEntry` row available to the RCA engine for that
incident was the single auto-generated "Incident auto-opened: ..." line
written by `detect_and_open_incident`. It does not contain words like
"timeout" or "db", so the `_rule_downstream_db_timeout` rule (which
requires log text matching those terms) never fired, and the engine fell
through to the more generic `elevated_error_rate` rule.

**This is not a bug, it's a scope boundary,** worth stating plainly in an
interview: real per-request application logs are not being ingested (no
ELK / log-aggregation pipeline, by design - see the project's stated
scope limits). The RCA engine is working correctly given the evidence it
actually has; the fix for better classification would be adding a real
log-ingestion path from each service (e.g. structured logs shipped to the
central platform), which is explicitly out of this project's scope and
called out here as a "what I'd improve" talking point instead.

---

## BUG-004: `AVAILABILITY_THRESHOLD` was documented as configurable but silently ignored

**Severity:** Medium (functionality worked, but a documented
non-functional requirement - "thresholds must come from environment
variables, not hard-coded values," see `docs/business_requirements.md`
- was violated for one of the three thresholds)

**Found during:** the Applied Materials interview-prep audit (static code
review, cross-checking every settings field against where it's actually
read), not a live drill.

**Reproduce:**
Set `AVAILABILITY_THRESHOLD=0.2` in the environment (meaning "only alert
if availability drops below 20%") and observe that an incident still
opens the moment availability drops below 50%, regardless of the
configured value.

**Evidence:**
`app/ingestion/collector.py`'s `detect_and_open_incident()` contained:
```python
if metrics["availability"] < 0.5:
```
a literal `0.5`, never referencing `settings.AVAILABILITY_THRESHOLD` at
all - even though `.env.example` and `docs/deployment.md` both described
`AVAILABILITY_THRESHOLD` (default `0.99`) as the value that controls
this check. `docs/deployment.md` had actually already flagged this
honestly in a parenthetical note rather than hiding it.

**Root cause:**
The `error_rate` and `latency_p95` thresholds were correctly wired to
`settings.ERROR_RATE_THRESHOLD` / `settings.LATENCY_P95_THRESHOLD_SECONDS`
when the detector was written; the availability check was left on its
original hard-coded literal and never updated to match.

**Fix:**
Replaced the literal with `settings.AVAILABILITY_THRESHOLD` in both the
comparison and the `trigger_threshold` value stored on the incident.

**Regression check:**
Added `tests/unit/test_incident_detection.py` (3 tests, run for real):
lowering the threshold below an observed availability now correctly
suppresses incident creation (proving the old hard-coded `0.5` cutoff is
gone), raising it still opens a CRITICAL incident with the configured
value recorded as `trigger_threshold`, and the existing
duplicate-incident guard still holds. Full suite (55 unit + 8
integration) re-run clean after the fix.

---

## BUG-005: `/admin/*` fault-injection endpoints had no authentication

**Severity:** High (security - not a functional defect, but a real
production-grade gap: any caller who could reach a service's port could
force it into a down/error state with zero credentials)

**Found during:** the audit's security review of every endpoint's
authentication/authorization (Phase 16 of the audit checklist).

**Reproduce (before the fix):**
```
curl -X POST "http://localhost:8003/admin/simulate-down?down=true"
```
succeeded with `200 {"simulate_down": true}` from any unauthenticated
caller, immediately taking payments-service's `/health` down and
triggering a real CRITICAL incident - i.e. an unauthenticated caller
could manufacture a production-looking outage on demand.

**Root cause:**
The fault-injection endpoints were built purely for the project's own
demo/drill workflow and were never given an authorization check, since
the original threat model assumed "only I will ever call these."

**Fix:**
Added a `FAULT_INJECTION_TOKEN` shared secret (env var, default
`dev-admin-token-change-me` for local dev) and a `require_admin_token`
FastAPI dependency, applied to every `/admin/*` route on all 4 services.
A missing or wrong `X-Admin-Token` header now returns `401`. Regular
business endpoints (`/login`, `/orders`, `/payments`, `/records`,
`/health`, `/metrics`) were deliberately left untouched - gating those
was never part of this fix and would have broken the monitoring flow
that reads `/health` and `/metrics` unauthenticated by design.

**Regression check:**
`tests/unit/test_admin_auth.py` (19 pytest-collected cases across all 4
services, run for real via FastAPI `TestClient`): missing token -> 401,
wrong token -> 401, correct token -> 200, and an explicit test proving
business endpoints are unaffected. Also manually verified live against a
running `db-proxy-service` process during this session (see terminal
output captured for this audit).

---

## BUG-006: `db-proxy-service POST /records` took an unvalidated raw query parameter

**Severity:** Low-Medium (API-design inconsistency + missing input
validation, not an active exploit given the parameterized SQL beneath
it - see the security audit's SQL-injection check in AUDIT_REPORT.md)

**Found during:** the audit's API-design review (Phase 7): this was the
only endpoint in the entire platform that took its body as a bare query
string (`payload: str` as a function parameter, which FastAPI treats as
a query param when no Pydantic model wraps it) instead of a JSON body.

**Evidence:**
`docs/api_docs.md` itself documented the inconsistency:
`POST /records?payload=...` versus every other write endpoint
(`/login`, `/orders`, `/payments`) using a JSON body.

**Root cause:**
Written early and never revisited once the pattern was established
elsewhere - a real example of "it works, so nobody circled back."

**Fix:**
Introduced `class RecordCreate(BaseModel): payload: str = Field(min_length=1, max_length=4096)`
and changed the handler to `def create_record(body: RecordCreate)`,
matching the JSON-body pattern used everywhere else, with a real length
bound (there previously was none).

**Regression check:**
`tests/unit/test_admin_auth.py`: valid JSON body accepted, empty
payload rejected (`422`), missing `payload` field rejected (`422`),
oversized (5000-char) payload rejected (`422`). Also manually verified
live: `curl -X POST /records -d '{"payload":"hello-world"}'` succeeded;
`curl ... -d '{"payload":""}'` returned `422`.

---

## BUG-007: Stored XSS in the frontend dashboard via unescaped `innerHTML`

**Severity:** Medium-High (security - the dashboard is the primary
demo/interview artifact, and this is a genuine stored-XSS pattern, even
though the current deployment has no real multi-user threat model)

**Found during:** the audit's frontend/security review (Phase 16 +
Phase 8/mockup review), by tracing where user-controlled data enters the
system and where it's rendered.

**Reproduce (before the fix):**
1. Upload a legacy CSV via `POST /conversion/legacy-incidents` with a
   `title` column containing `<img src=x onerror=alert(1)>`.
2. That string is validated (non-empty, matches `REQUIRED_COLUMNS`) and
   inserted verbatim as `Incident.title` - the CSV converter validates
   *shape*, not HTML-safety, which is correct on its part; sanitizing
   for a specific rendering target is the frontend's job, not the data
   layer's.
3. `frontend/js/app.js`'s `loadIncidents()` built table rows with
   `` `<td>${inc.title}</td>` `` and assigned the whole string straight
   to `tbody.innerHTML` - the browser would parse and execute the
   injected markup.

**Root cause:**
The dashboard was written to be fast to build, and every field
(service name, incident title, status, RCA text) was interpolated
directly into template-literal HTML with no escaping step anywhere in
the file.

**Fix:**
Added an `escapeHtml()` helper (creates a detached `<div>`, sets
`textContent`, reads back `innerHTML` - the standard escaping trick
that needs no library) and routed every interpolated, non-numeric,
non-enum field through it: service name/URL, incident title/status, RCA
root-cause category/confidence/summary/AI explanation. Also replaced an
inline `onclick="...incident.id..."` handler with a proper
`addEventListener` call, removing a second (lower-risk, since `id` is
numeric) inline-script pattern.

**Regression check:**
No headless-browser test harness exists in this project or environment,
so this fix is verified by code review only (confirmed every previously
unescaped interpolation site now passes through `escapeHtml()`) - stated
here honestly rather than claiming an automated test that doesn't exist.
Adding a headless-browser (e.g. Playwright) XSS regression test is
listed as a P2 item in IMPROVEMENT_PLAN.md.

---

## BUG-008: `sla_rules` table had zero effect on real incident deadlines

**Severity:** High (the core "SLA management" claim of this project was
not actually true for the one piece - the policy itself - that most
needed to be configurable)

**Found during:** an independent strict code-level audit of this
project (see `FINAL_IMPLEMENTATION_AUDIT.md` for the full audit this
upgrade responds to), specifically by tracing `calculate_deadlines()`'s
call site rather than trusting that a seeded, readable `sla_rules` table
implied it was actually used.

**Reproduce (before the fix):**
1. `GET /sla/rules` correctly showed the seeded policy (e.g. CRITICAL ->
   15/240 minutes) - a real, readable table.
2. Manually update that row in Postgres to a different value (e.g.
   CRITICAL -> 1/5 minutes).
3. Trigger a new CRITICAL incident.
4. Its `response_due_at`/`resolution_due_at` still reflected the
   *original* 15/240-minute policy, not the edited row.

**Root cause:**
`app/ingestion/collector.py::detect_and_open_incident()` called
`calculate_deadlines(opened_at, severity.value)` with no third argument.
`calculate_deadlines`'s signature is
`calculate_deadlines(opened_at, severity, sla_minutes: dict = None)`,
where `policy = sla_minutes or DEFAULT_SLA_MINUTES` - because the caller
never passed `sla_minutes`, every incident's deadlines were silently
computed from the hard-coded `DEFAULT_SLA_MINUTES` dict in
`app/sla/engine.py`, every time, regardless of what the `sla_rules`
table said. There was also no write endpoint for the table at all - it
was purely seeded-once-and-displayed.

**Fix:**
Added `app/sla/repository.py::get_sla_policy_for_severity(db, severity)`
- the one place that queries the live `sla_rules` row for a severity and
adapts it (via the new `sla/engine.py::sla_minutes_from_rule()`) into
the shape `calculate_deadlines()` expects, falling back to
`DEFAULT_SLA_MINUTES` with a logged warning only if no row exists at
all. `detect_and_open_incident()` now calls this before calculating
deadlines. Also added `PUT /sla/rules/{severity}` (ADMIN-only, audited)
so the table has a real, controlled write path instead of only being
editable by hand in the database.

**Regression check:**
`tests/unit/test_sla_repository.py` - 5 new tests, including
`test_changed_db_sla_rule_actually_changes_the_calculated_policy` (edits
a row, re-fetches, asserts the new value) and
`test_detect_and_open_incident_uses_the_live_db_policy_end_to_end` (runs
the real detection code path against a DB with a non-default rule and
asserts the resulting `Incident`'s due-dates match it, not the
built-in default). `tests/integration/test_api.py::test_sla_rule_update_accepted_for_admin_and_changes_the_value`
exercises the same thing through the real HTTP endpoint. All pass.

**What this means for how I'd phrase this project's SLA claim now:**
"SLA breach detection and deadline math were always real and tested;
the *policy* backing those deadlines was not actually live before this
fix, despite looking like it was from the API surface alone." Worth
saying unprompted in an interview if SLA comes up - see
`docs/interview_story.md`.
