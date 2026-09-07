# How to Present This Project in the Applied Materials Interview

Everything below is grounded in what's actually implemented and tested
- see `FINAL_IMPLEMENTATION_AUDIT.md` if you want the receipts behind
any specific claim before you say it out loud.

---

## 1. The 30-second version

"I built an IT operations command center - four small services stand in
for real business systems, and a central platform monitors them, opens
real incidents against real thresholds, calculates SLA deadlines from a
live database policy, runs a rule-based root-cause engine, has an LLM
explain the result in plain English, and requires a human with the
right role to verify it before it counts as confirmed. Everything's
logged to a real audit trail with real user identities."

## 2. The 60-second version

"It's an incident-management pipeline built on real, running
infrastructure, not a mockup. Four FastAPI services expose real health
checks, metrics, and structured logs, plus admin endpoints I can use to
inject real failures - kill a service, spike its latency, time out a
downstream dependency. A central platform polls all four, and when a
real threshold breaks, it opens an incident with a severity and an SLA
deadline pulled from a database table an admin can actually edit live.
A rule-based engine correlates the incident's real metrics and logs into
a root-cause category - and it's honest enough to say 'undetermined'
when the evidence doesn't support a clear answer, rather than guessing.
An LLM turns that into a readable explanation, constrained to only the
evidence it's given, and I run a second, deterministic check on its
output before a human reviewer signs off. Real login, four roles, and
every action - acknowledge, resolve, verify, even an SLA policy change -
writes to an audit log with the real user who did it."

## 3. The 2-minute version

Start with the 60-second version, then add:

"I didn't just build this once and call it done - I ran a strict,
adversarial audit against my own code afterward, the kind that assumes
nothing works until you can point to the line that proves it. It found
a genuinely embarrassing gap: I had a real, seeded, readable `sla_rules`
table in Postgres, and the SLA-deadline code never actually queried it -
it used a hard-coded Python dict instead, silently, the whole time. The
table *looked* like it configured behavior and didn't. I fixed that,
added the missing write endpoint, and wrote a test that proves editing
the table changes the next incident's real deadline. I did the same kind
of audit on my logging - my RCA rules that correlate metrics with log
text had almost nothing real to correlate against, because I was only
ever writing one synthetic log line per incident. So I gave each service
real structured logging tied to real events, and now the exact rule that
used to be nearly unreachable fires correctly from real data. I also
added real authentication - JWT, four roles matching different
operational responsibilities - because my audit log couldn't actually
say who did anything before that. The thing I'm most proud of isn't any
single feature - it's that the project now includes the process of
finding out what it actually does versus what I intended, and fixing the
difference, which is most of what production support work actually is."

## 4-11. Technology choices, specific to this project (not textbook answers)

**Why FastAPI:** free request validation via Pydantic caught a real bug
during this build (an unvalidated raw query param on `db-proxy-service`'s
`/records` endpoint - see `docs/bug_reports.md`, BUG-006) before it ever
needed a manual test to find it. Async support also matters for the
background poll loop running alongside the API in one process.

**Why PostgreSQL:** real foreign keys and enum types let the schema
itself reject an invalid severity or a dangling `service_id` - I wanted
constraints enforced by the database, not just application code, for
the parts of this system (incidents, audit trail) where data integrity
actually matters.

**Why Prometheus:** it's the de facto standard exposition format, and
using the real `prometheus_client` library in each service (not
hand-rolled counters) meant Grafana's dashboard queries could use normal
PromQL against real metric names, closer to what a real ops team would
already know.

**Why Grafana:** visualization decoupled from detection - genuinely
decoupled, in this build: the central platform never queries Prometheus
itself, it scrapes services directly. That's a deliberate resilience
choice I can explain if asked ("what happens if Prometheus goes down?"
- nothing, detection keeps working, only the dashboards go stale).

**Why Docker/Compose (not Kubernetes):** four services and a platform is
well within what Compose can express clearly; Kubernetes would add
operational surface area with zero benefit at this scale, and would
have cost me time I spent instead on the actual JD-relevant work
(RCA correctness, SLA logic, real auth).

**Why rule-based RCA, not ML or "just ask the LLM":** four services and
a handful of injected failure modes isn't enough labeled data to train
anything responsibly, and a hand-written rule is something I can show a
manager and have them understand exactly why it fired. Explainability
was the actual design goal, not a fallback.

**Why the LLM is only an explanation layer:** the rule engine decides
the category and confidence from structured evidence; the LLM's prompt
is built entirely from that structured result (`root_cause_category`,
`confidence`, the specific evidence items) and explicitly told not to
introduce anything else. If I'd just asked an LLM "what's the root
cause," I'd have no way to know whether its answer came from real
correlated data or from a plausible-sounding guess - and no consistent
way to test it. My rule engine's output is unit-tested; that's not
possible for "trust the model's judgment."

**Why human verification exists:** because a rule engine can be wrong
(it only checks a handful of hand-written conditions) and an LLM can
misstate even well-grounded evidence. Neither is treated as ground
truth - `verified_by_human` and `human_verdict` only ever get set by an
explicit action from an RCA_REVIEWER or ADMIN, never automatically. I
even added a second, deterministic layer (the AI-explanation validator)
specifically so a human reviewer isn't the *only* check between a
possibly-wrong AI sentence and the record - but that validator is a
"flag for review," never an auto-accept.

## 12. SLA explanation (lead with the fix, it's your best story)

"SLA policy is stored per-severity in Postgres, and an admin can change
it through a real endpoint - and here's the thing, that wasn't always
true. I found and fixed a bug where the table existed and looked
authoritative but the code silently used a hard-coded default instead.
I think that's actually a stronger thing to say than 'it just works' -
it shows I know how to verify a system does what it claims, not just
that I built the happy path once."

## 13. Failure injection explanation

"Each service has admin endpoints that cause real failures - not fake
metric writes. `simulate-down` actually returns 503 from `/health`;
`simulate-db-timeout` on the payments service actually sleeps 2 seconds
and returns 504, and logs the exact 'Payment database request exceeded
timeout' line my RCA rule engine looks for. I can walk through a full
drill live: inject the fault, show the metric spike in Grafana, show the
incident open with the SLA deadline pulled from the live policy, show
the log line get ingested, run RCA and get a specific, evidence-backed
category instead of a vague one, then clear the fault and show the
system detect recovery on its own - before I ever click anything."

## 14. Testing strategy

"89 unit tests covering every piece of pure logic - SLA math, the rule
engine, the AI-explanation validator, password hashing/JWT, recovery
detection - and 24 integration tests exercising the real router-to-
database path, including every auth/role boundary. I can also tell you
exactly what I didn't get to re-verify in this pass and why - see
`docs/testing.md` - rather than claim a green checkmark I don't actually
have."

## 15-16. Debugging stories (pick 1-2, tell them as a story, not a list)

**Story A - the SLA table:** see the 2-minute version above. Strong
because it's not "I found a typo," it's "a whole subsystem looked
correct from the outside and wasn't, and I built the specific test that
would have caught it originally."

**Story B - the missing python-multipart dependency (BUG-001):**
"My CSV-import endpoint used FastAPI's `UploadFile`, which needs
`python-multipart` installed - it's not a hard error until you actually
call that route, so the app *looked* fine everywhere else. My own
integration test suite caught it immediately because it actually calls
every endpoint, which is exactly the argument for writing integration
tests instead of only unit-testing each function in isolation."

**Story C - unauthenticated fault-injection endpoints (BUG-005):**
"Early on, anyone who knew a URL could force one of my services down or
corrupt its error rate - no token required. I found it by explicitly
reviewing the project for exactly that class of issue, added a shared-
secret header check, and wrote a parametrized test asserting all four
services' admin endpoints reject a missing/wrong token. It's a good
example of 'assume it's insecure until you can point to the check.'"

## 17. Security decisions (see docs/security.md for the full reasoning)

Lead with: real hashed passwords (PBKDF2, stdlib, explained trade-off vs
bcrypt), real JWT-based auth, four roles gating exactly the state-
changing endpoints that need it, and a clear statement of what this
is NOT (no MFA, no rate limiting, no secret manager - see the doc).
Interviewers respect "here's exactly where the line is" far more than
an unqualified "it's secure."

## 18. Scalability discussion

Don't claim it scales - explain what you'd change and why, using the
real bottleneck this project's own audit found: `GET /incidents` used to
issue one database commit per incident row on every single read; fixed
to one batched commit per page. Then talk about the poll loop being a
single in-process worker today, and what would need to change (a
dedicated poller process, a DB-level uniqueness constraint instead of a
check-then-insert guard) before running more than one central-platform
replica.

## 19. Current limitations (say these before they're asked)

- No automatic recovery -> resolution (by design - a human always
  confirms, but say this proactively, framed as a design choice not an
  oversight).
- Log ingestion is a small, bounded, in-memory buffer per service - fine
  at this scale, would need a real log pipeline at higher volume.
- No MFA, rate limiting, or secret manager (see docs/security.md).
- No Alembic - schema changes require a fresh DB in this project's
  current form (explained in `database/migrations/README.md`, and why
  that's a reasonable call for a project with no real production data).
- AI-explanation validation is deterministic and conservative, not a
  hallucination-free guarantee - correct phrase: "hallucination-
  resistant by construction, with deterministic evidence and human
  verification."

## 20. Production improvements (the "if I had more time" answer)

Alembic migrations, MFA + rate limiting + secret manager, a real log
pipeline if volume grew, read replicas + partitioned metrics/logs tables,
a dedicated poller process for HA, and distributed tracing across the 5
services. Say this crisply - a long list read verbatim sounds
memorized; picking the 2-3 most relevant to whatever the interviewer
just asked about sounds like understanding.

## 21-22. Likely interviewer questions and strong answers

**"How do you know your RCA isn't just making things up?"**
"Two separate layers, neither of which is the LLM's own judgment: the
rule engine only fires on specific, hard thresholds against real
correlated data, and separately, I run a deterministic check on the
LLM's text - if it names a metric or a number that wasn't in the
evidence it was given, or asserts confidence when the rule engine said
'undetermined,' that gets flagged for review. And regardless of both of
those, nothing is treated as fact until a human with the right role
explicitly verifies it."

**"What was the hardest bug to find, not just to fix?"**
The SLA-table disconnect - hard specifically because every symptom
looked fine: the table existed, was seeded, was readable via the API,
and the deadline math itself was correct and well-tested. The bug was
in what *fed* that correct math, which nothing exercised end-to-end
until an audit specifically asked "prove the DB row actually changes
behavior," not just "does this function compute the right number."

**"Why didn't you just use an existing incident-management tool?"**
This project exists to demonstrate the underlying mechanics - detection,
correlation, SLA math, explainable RCA, human-in-the-loop verification -
which building on top of an existing tool would have hidden rather than
shown.

**"What would you do differently if you started over?"**
Add the AI-explanation validator and the recovery/resolution distinction
from day one rather than as an upgrade pass - both came from thinking
harder about what "verified" and "resolved" actually mean, which I'd
now bake into the initial data model instead of retrofitting.
