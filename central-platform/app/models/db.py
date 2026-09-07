"""
Database models for the Central Platform.

Entities (from the project spec): users, services, incidents, logs, metrics,
sla_rules, recommendations, audit_logs.

Uses SQLAlchemy 2.0 style with a real PostgreSQL backend (see
app/core/config.py for the connection string). Relationships are modeled
explicitly with foreign keys so joins/aggregations used elsewhere in the
platform (e.g. "incidents per service", "recommendations for an incident")
are real relational queries, not application-side stitching.
"""
import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums - kept as real DB-level enums (constraint enforcement, not just app
# validation) so an invalid severity/status can never land in the table.
# ---------------------------------------------------------------------------
class Severity(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IncidentStatus(str, enum.Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    # System-detected: the health/metric check that originally triggered
    # this incident no longer breaches, observed by the poller itself -
    # NOT a human decision. Deliberately distinct from RESOLVED so
    # "the service came back on its own" and "a human signed off on it"
    # are never conflated (see docs/architecture.md, recovery section).
    RECOVERED = "recovered"
    RESOLVED = "resolved"
    BREACHED = "breached"


# Active/unresolved statuses for the purposes of (a) blocking a duplicate
# incident from opening on the same service and (b) checking for recovery.
# RESOLVED and RECOVERED are excluded from the duplicate-open guard: a
# RECOVERED incident that a human hasn't resolved yet is still visible on
# the dashboard, but if the same service fails again it's treated as a
# new event rather than silently reusing the old (already-recovered) row.
ACTIVE_INCIDENT_STATUSES = (IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED, IncidentStatus.BREACHED)


class UserRole(str, enum.Enum):
    """Roles map directly to the Applied Materials JD responsibilities
    this project demonstrates - see docs/applied_materials_jd_mapping.md.
    Deliberately 4 roles, not a general-purpose permission system."""
    ADMIN = "admin"                      # manage SLA policy, administrative actions
    IT_SUPPORT = "it_support"            # view + acknowledge incidents
    INCIDENT_MANAGER = "incident_manager"  # acknowledge + resolve, owns the lifecycle
    RCA_REVIEWER = "rca_reviewer"        # run RCA, verify/reject/modify recommendations


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.IT_SUPPORT)
    created_at = Column(DateTime, default=datetime.utcnow)

    audit_logs = relationship("AuditLog", back_populates="user")


class Service(Base):
    __tablename__ = "services"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False, index=True)
    base_url = Column(String(256), nullable=False)  # e.g. http://auth-service:8000
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Cursor for log ingestion (Priority 2): the highest per-service log
    # `seq` the collector has already pulled from this service's own
    # /logs endpoint and written into the `logs` table. Stored on the DB
    # row (not an in-process variable) so a central-platform restart
    # doesn't re-ingest everything from seq 0 again.
    last_ingested_log_seq = Column(Integer, nullable=False, default=0)

    incidents = relationship("Incident", back_populates="service")
    logs = relationship("LogEntry", back_populates="service")
    metrics = relationship("MetricSample", back_populates="service")


class SLARule(Base):
    __tablename__ = "sla_rules"
    __table_args__ = (UniqueConstraint("severity", name="uq_sla_rule_severity"),)

    id = Column(Integer, primary_key=True)
    severity = Column(Enum(Severity), nullable=False)
    response_minutes = Column(Integer, nullable=False)  # time to acknowledge
    resolution_minutes = Column(Integer, nullable=False)  # time to resolve
    description = Column(Text, nullable=True)


class Incident(Base):
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False, index=True)
    title = Column(String(256), nullable=False)
    severity = Column(Enum(Severity), nullable=False)
    status = Column(Enum(IncidentStatus), nullable=False, default=IncidentStatus.OPEN)

    opened_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    acknowledged_at = Column(DateTime, nullable=True)
    # System-detected recovery timestamp (Priority 3) - set by the poller
    # the moment it observes the triggering condition no longer breaches.
    # Distinct from resolved_at: a service can recover on its own long
    # before a human formally resolves the incident.
    recovered_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)

    response_due_at = Column(DateTime, nullable=False)
    resolution_due_at = Column(DateTime, nullable=False)
    response_breached = Column(Boolean, default=False)
    resolution_breached = Column(Boolean, default=False)

    trigger_metric = Column(String(128), nullable=True)  # e.g. "error_rate"
    trigger_value = Column(Float, nullable=True)
    trigger_threshold = Column(Float, nullable=True)

    service = relationship("Service", back_populates="incidents")
    recommendations = relationship("Recommendation", back_populates="incident")


class LogEntry(Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False, index=True)
    level = Column(String(16), nullable=False)  # INFO / WARN / ERROR
    message = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    service = relationship("Service", back_populates="logs")


class MetricSample(Base):
    __tablename__ = "metrics"

    id = Column(Integer, primary_key=True)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False, index=True)
    metric_name = Column(String(128), nullable=False)  # error_rate, latency_p95, availability
    value = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    service = relationship("Service", back_populates="metrics")


class Recommendation(Base):
    """
    Stores the RCA engine's output for an incident: the rule-based evidence
    trail AND the LLM-generated explanation, kept as separate columns so the
    UI can always show "what the rules found" distinctly from "what the AI
    said about it" (this separation is the hallucination-mitigation
    guardrail described in the spec: evidence -> rules -> AI explanation ->
    human decision).
    """
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True)
    incident_id = Column(Integer, ForeignKey("incidents.id"), nullable=False, index=True)

    rule_based_summary = Column(Text, nullable=False)
    evidence_json = Column(Text, nullable=False)  # JSON-encoded evidence list
    ai_explanation = Column(Text, nullable=True)
    ai_confidence_note = Column(Text, nullable=True)  # explicit uncertainty statement
    # Priority 5 - lightweight, deterministic evidence-consistency check on
    # the LLM's own explanation text (see app/rca/ai_validator.py). NOT a
    # hallucination-free guarantee - a second, cheap, explainable check
    # layered on top of the grounding prompt, before a human ever sees it.
    ai_validation_status = Column(String(16), nullable=True)  # PASSED | FLAGGED | UNAVAILABLE
    ai_validation_reason = Column(Text, nullable=True)
    verified_by_human = Column(Boolean, default=False)
    human_verdict = Column(Text, nullable=True)  # accepted / rejected / modified + note

    created_at = Column(DateTime, default=datetime.utcnow)

    incident = relationship("Incident", back_populates="recommendations")


class AuditLog(Base):
    """
    Every state-changing action (incident ack/resolve, SLA override, RCA
    verification) is written here. This is what "adheres to SLA processes"
    is defensible against in an interview - not a claim, a queryable trail.
    """
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String(128), nullable=False)
    target_type = Column(String(64), nullable=False)  # "incident", "sla_rule", etc.
    target_id = Column(Integer, nullable=True)
    details = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User", back_populates="audit_logs")
