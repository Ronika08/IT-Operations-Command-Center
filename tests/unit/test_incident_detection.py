"""
Regression test for a real bug found during the audit: the incident
detector's availability check used a hard-coded `0.5` literal instead of
`settings.AVAILABILITY_THRESHOLD`, even though that env var was
documented as configurable (.env.example / docs/deployment.md) and had
its own default (0.99). The threshold was silently unconfigurable.

Uses an isolated in-memory SQLite database (via SQLAlchemy) so this runs
without a live Postgres instance, matching the rest of the unit suite.
"""
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "central-platform"))

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.ingestion import collector  # noqa: E402
from app.models.db import Base, Incident, Service  # noqa: E402


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_availability_threshold_is_read_from_settings_not_hardcoded():
    """Regression for the fixed bug: lowering AVAILABILITY_THRESHOLD to
    something below the observed availability must mean NO incident is
    opened, even though the old hard-coded `< 0.5` check would have
    opened one for any availability under 50%. This proves the setting
    is actually wired in, not just documented."""
    db = _make_session()
    service = Service(name="orders-service", base_url="http://orders-service:8000")
    db.add(service)
    db.commit()
    db.refresh(service)

    original_threshold = collector.settings.AVAILABILITY_THRESHOLD
    try:
        # availability=0.3 - the OLD hard-coded rule (< 0.5) would have
        # opened a CRITICAL incident here regardless of configuration.
        collector.settings.AVAILABILITY_THRESHOLD = 0.2
        metrics = {"error_rate": 0.0, "latency_p95": 0.0, "availability": 0.3}
        incident = collector.detect_and_open_incident(db, service, metrics)
        assert incident is None, (
            "availability (0.3) is above the configured threshold (0.2) - "
            "no incident should open. If this fails, the hard-coded 0.5 "
            "cutoff has regressed back in."
        )
    finally:
        collector.settings.AVAILABILITY_THRESHOLD = original_threshold


def test_availability_threshold_still_opens_incident_when_breached():
    db = _make_session()
    service = Service(name="payments-service", base_url="http://payments-service:8000")
    db.add(service)
    db.commit()
    db.refresh(service)

    original_threshold = collector.settings.AVAILABILITY_THRESHOLD
    try:
        collector.settings.AVAILABILITY_THRESHOLD = 0.99
        metrics = {"error_rate": 0.0, "latency_p95": 0.0, "availability": 0.95}
        incident = collector.detect_and_open_incident(db, service, metrics)
        assert incident is not None
        assert incident.severity.value == "critical"
        assert incident.trigger_metric == "availability"
        assert incident.trigger_threshold == 0.99
    finally:
        collector.settings.AVAILABILITY_THRESHOLD = original_threshold


def test_no_duplicate_incident_opened_while_one_already_open():
    db = _make_session()
    service = Service(name="auth-service", base_url="http://auth-service:8000")
    db.add(service)
    db.commit()
    db.refresh(service)

    metrics = {"error_rate": 0.0, "latency_p95": 0.0, "availability": 0.0}
    first = collector.detect_and_open_incident(db, service, metrics)
    second = collector.detect_and_open_incident(db, service, metrics)

    assert first is not None
    assert second is None  # duplicate-incident guard must hold
    assert db.query(Incident).filter(Incident.service_id == service.id).count() == 1
