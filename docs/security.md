# Security

This document covers what this project's security model actually is,
the reasoning behind each choice, and - just as importantly - what it
explicitly is not.

## Authentication (Priority 4 of this upgrade)

- **Login:** `POST /auth/login` checks a submitted password against a
  real, hashed value in the `users` table and issues a JWT
  (`app/core/security.py::create_access_token`). There is no
  registration endpoint - accounts are seeded once at startup (see
  "Demo accounts" below), because this is a controlled interview demo,
  not a self-service product.
- **Password hashing:** `hashlib.pbkdf2_hmac("sha256", ..., iterations=260_000)`
  with a random 16-byte salt per user, stored as
  `pbkdf2_sha256$<iterations>$<salt>$<hash>`. This is a real, standard,
  non-reversible, salted hash (260k iterations matches Django's own
  current default) - not a toy. **Why not bcrypt/argon2:** those would
  add a new compiled dependency for a project whose brief explicitly
  asks not to add technologies just to look more impressive; PBKDF2 via
  the stdlib is a legitimate, defensible choice at this scope.
  bcrypt/argon2id is the correct production upgrade and is listed as
  such below.
- **Tokens:** JWT (HS256), signed with `JWT_SECRET_KEY`, carrying only
  `user_id`/`username`/`role`/`exp` - never the password or its hash.
  Verified on every protected request (`app/core/deps.py::get_current_user`),
  which also re-loads the real `User` row so a token for a
  deleted/disabled user is rejected even if it hasn't technically
  expired yet.

## Authorization (RBAC)

Four fixed roles, matching the JD responsibilities this project
demonstrates (see `docs/applied_materials_jd_mapping.md`):

| Role | Can do |
|---|---|
| `ADMIN` | Everything IT_SUPPORT/INCIDENT_MANAGER/RCA_REVIEWER can, plus edit SLA policy (`PUT /sla/rules/{severity}`) and import legacy CSV data |
| `IT_SUPPORT` | View everything, acknowledge incidents |
| `INCIDENT_MANAGER` | View everything, acknowledge + resolve incidents |
| `RCA_REVIEWER` | View everything, run RCA (any role can, actually - see below), verify/reject/modify RCA recommendations |

**Design choice - read endpoints require login but not a specific
role:** `GET /incidents`, `/services`, `/sla/rules`, `/sla/summary`,
`/audit-logs` all require a valid token but accept any role. Viewing
operational state is useful to every role in this project, and there's
no meaningful confidentiality boundary between them (unlike, say, salary
data) - the real access-control question here is who can *change*
things, which every state-changing endpoint enforces individually.
`/health` stays fully public (standard for infra liveness checks, and
leaks nothing sensitive).

**Design choice - running RCA is not role-gated, verifying it is:**
Running RCA only produces a suggestion for a human to review - it never
changes anything on its own - so it's treated like a read operation.
*Verifying* (accepting/rejecting/modifying) that suggestion is the
consequential action, and is RCA_REVIEWER/ADMIN-only.

## Audit trail

Every state-changing action (login is not itself audited, but every
action *after* login is) writes a real `AuditLog` row with the real
authenticated user's id - not a value the caller can supply, which is
exactly what changed in this upgrade (`AckRequest`/`ResolveRequest`/
`VerifyRequest` no longer accept a `user_id` field at all; it comes from
the verified JWT). The one exception, by design: `auto_recovery_detected`
always has `user_id = NULL`, because it's a system-observed fact, not a
human action - see `docs/architecture.md` for why that distinction is
preserved rather than attributing it to some default account.

## Demo accounts

Seeded once at startup if the `users` table is empty
(`main.py::seed_reference_data`), one per role, username == role name.
Passwords come from environment variables
(`SEED_ADMIN_PASSWORD`/`SEED_IT_SUPPORT_PASSWORD`/
`SEED_INCIDENT_MANAGER_PASSWORD`/`SEED_RCA_REVIEWER_PASSWORD`), never
hard-coded in source. If an env var is absent, a default placeholder
(e.g. `changeme-admin`) is used **and a warning is logged** naming
exactly which var to set - so using the default is visible in the
startup logs, not a silent trap.

## Fault-injection auth (separate mechanism - don't confuse the two)

The 4 backend services' `/admin/*` endpoints (inject-latency,
inject-errors, simulate-down, etc.) are gated by a *different*,
simpler shared-secret header (`X-Admin-Token`, matching each service's
own `FAULT_INJECTION_TOKEN` env var) - this predates and is unrelated to
the central platform's JWT login. It exists to stop an unauthenticated
caller from forcing a demo service down, not to model real user
identity, and is intentionally left as a simple shared secret rather
than folded into the JWT system, since these endpoints belong to
separate "business" services standing in for systems this project
doesn't actually own.

## Other security items already in place (from the earlier audit round)

- Parameterized queries throughout (SQLAlchemy ORM) - no raw string-built
  SQL, no SQL injection found in the code-level audit.
- Input validation via Pydantic on every endpoint (rejects malformed
  bodies with 422 before any handler code runs).
- Frontend XSS fix: every interpolated field goes through `escapeHtml()`
  before being placed into the DOM (see `docs/bug_reports.md`, BUG-007).
- `.env` is gitignored; `.env.example` contains placeholders only - no
  real Gemini key or real secret is included in this repository or ZIP.
- Generic, non-leaking error messages from the LLM call path (the raw
  exception is never returned to the client).

## What this is NOT (said plainly, not hidden)

- **Not enterprise IAM.** No SSO, no MFA, no password reset flow, no
  account lockout after repeated failed logins, no session revocation
  list (a JWT is valid until it expires, full stop - there's no
  server-side "log this token out early" mechanism).
- **Not encrypted at rest.** Postgres data volume encryption is a
  deployment-environment concern, not something this app configures.
- **Not rate-limited.** Login and every other endpoint can be hit as
  fast as the caller likes - no brute-force protection.
- **CORS is wide open (`*`) by default**, appropriate for a local
  docker-compose demo across ports, not for a real multi-origin
  deployment - tightening this to the actual frontend origin is a
  one-line production change, listed here rather than done by default
  since the default needs to keep working for anyone who just clones
  this and runs `docker compose up`.
- **Not a threat-modeled system.** No formal STRIDE-style analysis was
  performed; the fixes above address the specific, concrete issues found
  by direct code inspection, not a systematic security review.

## Production evolution (what would actually need to change)

1. Real password policy (complexity/rotation) + account lockout.
2. bcrypt/argon2id instead of PBKDF2.
3. Short-lived access tokens + refresh tokens, with server-side
   revocation (e.g. a denylist in Redis) instead of "valid until it
   naturally expires."
4. MFA for ADMIN.
5. CORS locked to the real frontend origin(s).
6. Rate limiting (login especially).
7. Secrets in a real secret manager (Vault/AWS Secrets Manager/etc.),
   not environment variables in a `.env` file.
8. TLS termination in front of every service.
9. A real audit-log retention/export story for compliance, not just an
   unbounded Postgres table.
