"""
Unit tests for automatic recovery detection (Priority 3).

Covers exactly the scenarios called for in the brief: service stays
down (no false recovery), service recovers (marked RECOVERED, timestamped,
audited), recovery fires exactly once, and RECOVERED is never confused
with RESOLVED (a human action).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "central-platform"))

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.ingestion import collector  # noqa: E402
from app.models.db import AuditLog, Base, Incident, IncidentStatus, LogEntry, Service  # noqa: E402


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _make_service(db, name="auth-service"):
    service = Service(name=name, base_url=f"http://{name}:8000")
    db.add(service)
    db.commit()
    db.refresh(service)
    return service


DOWN_METRICS = {"error_rate": 0.0, "latency_p95": 0.0, "availability": 0.0}
HEALTHY_METRICS = {"error_rate": 0.0, "latency_p95": 0.0, "availability": 1.0}


def test_check_recovery_does_nothing_when_no_active_incident():
    db = _make_session()
    service = _make_service(db)
    result = collector.check_recovery(db, service, HEALTHY_METRICS)
    assert result is None


def test_service_remains_down_is_not_falsely_marked_recovered():
    db = _make_session()
    service = _make_service(db)
    incident = collector.detect_and_open_incident(db, service, DOWN_METRICS)
    assert incident is not None

    result = collector.check_recovery(db, service, DOWN_METRICS)  # still down
    assert result is None
    db.refresh(incident)
    assert incident.status == IncidentStatus.OPEN
    assert incident.recovered_at is None


def test_service_recovering_marks_incident_recovered_not_resolved():
    db = _make_session()
    service = _make_service(db)
    incident = collector.detect_and_open_incident(db, service, DOWN_METRICS)

    recovered = collector.check_recovery(db, service, HEALTHY_METRICS)

    assert recovered is not None
    assert recovered.id == incident.id
    assert recovered.status == IncidentStatus.RECOVERED
    assert recovered.recovered_at is not None
    # The critical distinction the brief calls out explicitly:
    assert recovered.status != IncidentStatus.RESOLVED
    assert recovered.resolved_at is None


def test_recovery_is_detected_exactly_once():
    """Once RECOVERED, the incident is no longer 'active', so a second
    healthy poll must not re-fire recovery logic (no duplicate audit/log
    rows, no re-touching recovered_at)."""
    db = _make_session()
    service = _make_service(db)
    incident = collector.detect_and_open_incident(db, service, DOWN_METRICS)

    first = collector.check_recovery(db, service, HEALTHY_METRICS)
    first_recovered_at = first.recovered_at

    second = collector.check_recovery(db, service, HEALTHY_METRICS)
    assert second is None  # no active incident left to recover - guard holds

    db.refresh(incident)
    assert incident.recovered_at == first_recovered_at  # untouched by the second poll

    recovery_audit_rows = db.query(AuditLog).filter(AuditLog.action == "auto_recovery_detected").all()
    assert len(recovery_audit_rows) == 1


def test_recovery_writes_a_system_log_entry_and_audit_row_with_no_user():
    db = _make_session()
    service = _make_service(db)
    collector.detect_and_open_incident(db, service, DOWN_METRICS)
    collector.check_recovery(db, service, HEALTHY_METRICS)

    recovery_logs = db.query(LogEntry).filter(LogEntry.level == "INFO", LogEntry.message.like("%recovered%")).all()
    assert len(recovery_logs) == 1

    audit_row = db.query(AuditLog).filter(AuditLog.action == "auto_recovery_detected").first()
    assert audit_row is not None
    # System-detected, not a human action - user_id must be None, not a
    # fabricated id (see docs/security.md on what NULL user_id means now).
    assert audit_row.user_id is None


def test_recovered_incident_does_not_block_a_new_incident_on_relapse():
    """If the service fails again after recovering (before a human
    resolves it), the duplicate-open guard must not silently suppress a
    new incident - RECOVERED is excluded from ACTIVE_INCIDENT_STATUSES on
    purpose (see models/db.py)."""
    db = _make_session()
    service = _make_service(db)
    first = collector.detect_and_open_incident(db, service, DOWN_METRICS)
    collector.check_recovery(db, service, HEALTHY_METRICS)

    second = collector.detect_and_open_incident(db, service, DOWN_METRICS)
    assert second is not None
    assert second.id != first.id


def test_poll_all_services_runs_recovery_check_before_reopening():
    """End-to-end through the real scheduled entrypoint (poll_all_services),
    using a stubbed poll_service so this test doesn't need real HTTP."""
    db = _make_session()
    service = _make_service(db)
    collector.detect_and_open_incident(db, service, DOWN_METRICS)

    original_poll_service = collector.poll_service
    original_ingest_logs = collector.ingest_logs
    collector.poll_service = lambda _db, _svc: dict(HEALTHY_METRICS)
    collector.ingest_logs = lambda _db, _svc: 0
    try:
        collector.poll_all_services(db)
    finally:
        collector.poll_service = original_poll_service
        collector.ingest_logs = original_ingest_logs

    incident = db.query(Incident).filter(Incident.service_id == service.id).first()
    assert incident.status == IncidentStatus.RECOVERED
