# RCA Reports (from actual failure-injection drills)

## Drill 1: Forced 100% error rate on payments-service

**Injection:** `POST http://localhost:8003/admin/inject-errors?rate=1.0`,
followed by 5 real POST requests to `/payments`.

**Detection:** Because the fault-injection middleware applies to *every*
request including `/health`, the health check itself started failing,
so the collector read `availability=0.0`. The detector's first rule
(availability < 0.5 -> CRITICAL) matched, opening:

```json
{
  "id": 1,
  "title": "payments-service is unavailable",
  "severity": "critical",
  "trigger_metric": "availability",
  "trigger_value": 0.0,
  "trigger_threshold": 0.5,
  "response_due_at": "+15 minutes",
  "resolution_due_at": "+240 minutes"
}
```

**RCA rule engine result:** `service_outage`, confidence `high` - correct
given the evidence (availability metric was genuinely near zero).

**Judgment call for a human:** in a real incident, an operator would
still need to ask *why* availability dropped - a service can be
"unavailable" from the monitor's point of view for very different real
causes (crashed process, network partition, or - as here - a bad
deploy that turned every request, including health checks, into a
500). The rule engine correctly flags the symptom category
(`service_outage`); root-causing *why* it's down still requires reading
the actual deploy/change log, which is outside this project's scope but
exactly the kind of gap a real on-call engineer has to fill.

---

## Drill 2: Simulated downstream DB timeout on payments-service

**Injection:** `POST http://localhost:8003/admin/simulate-db-timeout?on=true`,
then one real POST to `/payments` (returned HTTP 504, confirmed live).

**Detection:** Error rate crossed the 10% threshold (measured
`error_rate=0.20`, later `0.143` after more traffic), opening a HIGH
incident: `"payments-service error rate elevated"`.

**RCA rule engine result:** `elevated_error_rate`, confidence `medium` -
**not** `downstream_dependency_failure`, even though the underlying
injected fault genuinely was a simulated DB timeout.

**Root cause of the misclassification (documented honestly, see
BUG-003 in docs/bug_reports.md):** the RCA engine's DB-timeout rule needs
log lines containing words like "timeout" or "db" in the incident's time
window. The only log row available was the auto-generated
"Incident auto-opened: ... (error_rate=0.2, threshold=0.1)" line, which
doesn't mention the underlying cause - because this project does not
ingest each service's real per-request application logs (out of scope,
no ELK stack). The rule engine did the right thing with the evidence it
had; it just didn't have the evidence that would have pointed at the
more specific category.

**What I'd say in an interview about this:** this is the difference
between a system being "wrong" and a system being "honest about its
blind spots." The engine never claimed high confidence in a cause it
couldn't support - it fell back to the correctly-supported, more generic
category (`elevated_error_rate`, medium confidence) rather than
guessing `downstream_dependency_failure` without log evidence for it.
That's the hallucination-mitigation principle applied to the rule layer,
not just the LLM layer.

---

## Drill 3 (unit-tested, deliberately ambiguous): no clear signal

**Scenario (see `tests/unit/test_rca_rules.py::test_ambiguous_case_returns_undetermined_not_a_guess`):**
metrics within normal bounds (`error_rate=0.03`, `latency_p95=0.4`,
`availability=0.995`) and logs that are unrelated noise
("scheduled cache refresh completed", "healthcheck ok").

**RCA rule engine result:** `undetermined`, confidence `low`, zero
evidence items, with an explicit statement that this needs human
investigation.

**Why this matters for defensibility:** an engine that always returns
*some* category, even under ambiguous input, is far more dangerous than
one that says "I don't know" - because a false category actively misleads
an on-call engineer toward the wrong fix. This is the explicit design
choice discussed in `app/rca/rules.py`'s docstring and is the single
best answer to "how do you prevent this system from confidently being
wrong."

---

## Drill 4 (this upgrade): the Drill 2 misclassification, fixed

**What changed:** Priority 2 of this upgrade gave each service a real
structured log buffer (`app/applog.py`) and wired the central platform to
ingest it. `payments-service`'s `simulate-db-timeout` fault now writes a
real `ERROR "Payment database request exceeded timeout"` log line at the
exact moment a request actually times out - not a fabricated line added
to satisfy the rule engine, but the service's own real account of what
happened to that request.

**Re-running Drill 2's exact scenario** (see
`tests/unit/test_service_logs.py::test_rca_engine_correlates_real_payments_service_logs_for_db_timeout`,
which does this against the real service code, not a hand-written log
fixture):

1. `POST /admin/simulate-db-timeout?on=true`
2. `POST /payments` (a real request, which now really does sleep 2s and
   return 504, and logs the ERROR above)
3. Pull the service's own `/logs` - the real log list now contains that
   ERROR line.
4. Feed the real `latency_p95` (2.0s, correctly above the 1.5s rule
   threshold) and that real log line into `rca/rules.py::analyze()`.

**RCA rule engine result now:** `downstream_dependency_failure`,
confidence **high** - the correct, specific category this incident
always deserved, reached without changing a single line of
`rca/rules.py` itself. The fix was entirely upstream: giving the rule
engine the real evidence it was always designed to use. This is the
concrete proof that Priority 2 closed the gap Drill 2 first surfaced.

---

## Drill 5 (this upgrade): automatic recovery, kept distinct from resolution

**Scenario** (see `tests/unit/test_recovery_detection.py`): a service is
forced unavailable (`availability=0.0`), which opens a CRITICAL incident
as usual. The fault is then cleared and a subsequent poll observes
`availability=1.0`.

**Result:** `check_recovery()` marks the incident `RECOVERED` (not
`RESOLVED`), sets a real `recovered_at` timestamp, and writes an
`AuditLog` row with `action="auto_recovery_detected"` and `user_id=NULL`
- explicitly a system fact, not a human decision. A second healthy poll
does not re-fire recovery (it's no longer in the "active" status set), and
a human (`incident_manager`/`admin`) still has to call `/resolve`
separately to close it. If that never happens before the resolution SLA
deadline passes, the incident's status can still become `BREACHED`
later - recovering quickly doesn't quietly erase an SLA miss if nobody
closes the loop.

