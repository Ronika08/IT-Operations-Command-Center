-- IT Operations Command Center - initial schema
-- This mirrors central-platform/app/models/db.py exactly. The application
-- itself creates tables via SQLAlchemy's Base.metadata.create_all() on
-- startup; this file exists so the schema can be reviewed, diffed, and run
-- independently of the app (e.g. by a DBA, or in a CI schema-lint step).
--
-- NOTE: this is the schema as of the initial build. See
-- 002_operational_upgrades.sql for the changes added afterward (recovery
-- detection, the 4-role RBAC model, AI validation columns). Both files are
-- kept, in order, rather than editing this one in place, so the history of
-- schema changes stays visible - see database/migrations/README.md for why
-- there is no automated migration runner (Alembic) applying these.

CREATE TYPE severity_enum AS ENUM ('critical', 'high', 'medium', 'low');
CREATE TYPE incident_status_enum AS ENUM ('open', 'acknowledged', 'resolved', 'breached');
CREATE TYPE user_role_enum AS ENUM ('admin', 'operator', 'viewer');

CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    username        VARCHAR(64) UNIQUE NOT NULL,
    password_hash   VARCHAR(256) NOT NULL,
    role            user_role_enum NOT NULL DEFAULT 'viewer',
    created_at      TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX idx_users_username ON users(username);

CREATE TABLE services (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(64) UNIQUE NOT NULL,
    base_url        VARCHAR(256) NOT NULL,
    description     TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX idx_services_name ON services(name);

CREATE TABLE sla_rules (
    id                  SERIAL PRIMARY KEY,
    severity            severity_enum NOT NULL,
    response_minutes    INTEGER NOT NULL,
    resolution_minutes  INTEGER NOT NULL,
    description         TEXT,
    CONSTRAINT uq_sla_rule_severity UNIQUE (severity)
);

CREATE TABLE incidents (
    id                      SERIAL PRIMARY KEY,
    service_id              INTEGER NOT NULL REFERENCES services(id),
    title                   VARCHAR(256) NOT NULL,
    severity                severity_enum NOT NULL,
    status                  incident_status_enum NOT NULL DEFAULT 'open',
    opened_at               TIMESTAMP NOT NULL DEFAULT now(),
    acknowledged_at         TIMESTAMP,
    resolved_at             TIMESTAMP,
    response_due_at         TIMESTAMP NOT NULL,
    resolution_due_at       TIMESTAMP NOT NULL,
    response_breached       BOOLEAN NOT NULL DEFAULT FALSE,
    resolution_breached     BOOLEAN NOT NULL DEFAULT FALSE,
    trigger_metric          VARCHAR(128),
    trigger_value           DOUBLE PRECISION,
    trigger_threshold       DOUBLE PRECISION
);
CREATE INDEX idx_incidents_service_id ON incidents(service_id);
CREATE INDEX idx_incidents_status ON incidents(status);

CREATE TABLE logs (
    id              SERIAL PRIMARY KEY,
    service_id      INTEGER NOT NULL REFERENCES services(id),
    level           VARCHAR(16) NOT NULL,
    message         TEXT NOT NULL,
    "timestamp"     TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX idx_logs_service_id ON logs(service_id);
CREATE INDEX idx_logs_timestamp ON logs("timestamp");

CREATE TABLE metrics (
    id              SERIAL PRIMARY KEY,
    service_id      INTEGER NOT NULL REFERENCES services(id),
    metric_name     VARCHAR(128) NOT NULL,
    value           DOUBLE PRECISION NOT NULL,
    "timestamp"     TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX idx_metrics_service_id ON metrics(service_id);
CREATE INDEX idx_metrics_timestamp ON metrics("timestamp");

CREATE TABLE recommendations (
    id                      SERIAL PRIMARY KEY,
    incident_id             INTEGER NOT NULL REFERENCES incidents(id),
    rule_based_summary      TEXT NOT NULL,
    evidence_json           TEXT NOT NULL,
    ai_explanation          TEXT,
    ai_confidence_note      TEXT,
    verified_by_human       BOOLEAN NOT NULL DEFAULT FALSE,
    human_verdict           TEXT,
    created_at              TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX idx_recommendations_incident_id ON recommendations(incident_id);

CREATE TABLE audit_logs (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id),
    action          VARCHAR(128) NOT NULL,
    target_type     VARCHAR(64) NOT NULL,
    target_id       INTEGER,
    details         TEXT,
    "timestamp"     TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_logs_timestamp ON audit_logs("timestamp");

-- Seed default SLA policy (matches app/sla/engine.py DEFAULT_SLA_MINUTES)
INSERT INTO sla_rules (severity, response_minutes, resolution_minutes, description) VALUES
    ('critical', 15, 240,  'Default critical SLA policy'),
    ('high',     30, 480,  'Default high SLA policy'),
    ('medium',   120, 1440, 'Default medium SLA policy'),
    ('low',      480, 4320, 'Default low SLA policy');
