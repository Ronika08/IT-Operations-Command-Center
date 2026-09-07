-- IT Operations Command Center - operational upgrades
-- Adds the schema changes needed for: Priority 3 (automatic recovery
-- detection), Priority 4 (real 4-role auth/RBAC, replacing the unused
-- admin/operator/viewer roles), Priority 5 (AI explanation validation),
-- and Priority 2 (a durable per-service log-ingestion cursor).
--
-- As with 001_initial_schema.sql, this is a REFERENCE mirror of
-- central-platform/app/models/db.py, not something the app runs
-- automatically - see README.md in this folder for what that means in
-- practice and why.

-- Priority 3: recovery is a new incident state, distinct from RESOLVED.
ALTER TYPE incident_status_enum ADD VALUE IF NOT EXISTS 'recovered';
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS recovered_at TIMESTAMP;

-- Priority 4: replace the old, never-actually-used admin/operator/viewer
-- roles with the 4 roles the RBAC model actually uses. On a genuinely
-- fresh install this is moot (init_db() creates the enum with the new
-- values directly) - this ALTER path exists for a database that already
-- has the old enum type and needs to be brought up to date without a
-- full drop/recreate.
ALTER TYPE user_role_enum ADD VALUE IF NOT EXISTS 'it_support';
ALTER TYPE user_role_enum ADD VALUE IF NOT EXISTS 'incident_manager';
ALTER TYPE user_role_enum ADD VALUE IF NOT EXISTS 'rca_reviewer';
-- NOTE: Postgres cannot drop enum values in-place. 'operator' and 'viewer'
-- are left defined but unused going forward - harmless, since no row ever
-- used them (see docs/bug_reports.md / the audit's "decorative users
-- table" finding - there were never any real User rows before this
-- change). A true cleanup would require creating a new enum type,
-- migrating the column over, and dropping the old type - not worth the
-- risk for a project with no real production data to preserve.

-- Priority 5: AI explanation validation result, stored alongside the
-- explanation it was checked against.
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS ai_validation_status VARCHAR(16);
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS ai_validation_reason TEXT;

-- Priority 2: durable cursor so the collector never re-ingests (or
-- silently drops, after a restart) log entries already pulled from a
-- service's /logs endpoint.
ALTER TABLE services ADD COLUMN IF NOT EXISTS last_ingested_log_seq INTEGER NOT NULL DEFAULT 0;
