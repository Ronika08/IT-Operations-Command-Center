"""
Regression tests for the Priority 1 fix: incident deadlines must come
from the live `sla_rules` table, not the hard-coded DEFAULT_SLA_MINUTES
dict. Before this fix, `sla_rules` was a real, seeded, readable table
that had NO effect on actual behavior (see docs/bug_reports.md, the SLA
finding from the audit).

These tests prove the fix two ways:
  1. Changing a SLARule row changes the deadlines of the NEXT incident
     opened for that severity (test_changed_db_sla_rule_...).
  2. A missing SLARule row falls back safely to the built-in default,
     with a logged warning, rather than crashing incident creation.
"""
import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "central-platform"))

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.models.db import Base, SLARule, Severity  # noqa: E402
from app.sla.engine import DEFAULT_SLA_MINUTES, sla_minutes_from_rule  # noqa: E402
from app.sla.repository import get_sla_policy_for_severity  # noqa: E402


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_sla_minutes_from_rule_adapts_a_db_row_shape():
    """Pure adapter test - no DB needed, matches the project's own
    'pure logic stays independently unit-testable' convention."""
    fake_row = type("FakeRule", (), {
        "severity": Severity.CRITICAL, "response_minutes": 7, "resolution_minutes": 99,
    })()
    policy = sla_minutes_from_rule(fake_row)
    assert policy[Severity.CRITICAL] == {"response": 7, "resolution": 99}


def test_get_sla_policy_reads_the_real_seeded_row():
    db = _make_session()
    db.add(SLARule(severity=Severity.HIGH, response_minutes=30, resolution_minutes=480))
    db.commit()

    policy = get_sla_policy_for_severity(db, Severity.HIGH)
    assert policy[Severity.HIGH] == {"response": 30, "resolution": 480}


def test_changed_db_sla_rule_actually_changes_the_calculated_policy():
    """THE core Priority 1 proof: this is exactly the scenario the audit
    found broken - a human changes a row in sla_rules, and the very next
    lookup for that severity must reflect the new value, not the old
    hard-coded default."""
    db = _make_session()
    rule = SLARule(severity=Severity.CRITICAL, response_minutes=15, resolution_minutes=240)
    db.add(rule)
    db.commit()

    original_policy = get_sla_policy_for_severity(db, Severity.CRITICAL)
    assert original_policy[Severity.CRITICAL]["response"] == 15

    # An admin changes the policy via PUT /sla/rules/critical (see
    # routers/sla.py::update_rule) - simulated here as a direct row edit,
    # since that endpoint's own logic is exactly this.
    rule.response_minutes = 5
    rule.resolution_minutes = 60
    db.commit()

    updated_policy = get_sla_policy_for_severity(db, Severity.CRITICAL)
    assert updated_policy[Severity.CRITICAL] == {"response": 5, "resolution": 60}
    assert updated_policy[Severity.CRITICAL] != original_policy[Severity.CRITICAL]


def test_missing_sla_rule_falls_back_safely_to_default():
    """No SLARule row exists at all (e.g. a fresh DB before startup
    seeding runs) - must not crash, and must fall back to the same
    numeric policy the whole project always used before this fix, so
    behavior never regresses for someone who hasn't touched the table."""
    db = _make_session()  # no SLARule rows inserted at all
    policy = get_sla_policy_for_severity(db, Severity.LOW)
    assert policy[Severity.LOW] == DEFAULT_SLA_MINUTES[Severity.LOW]


def test_detect_and_open_incident_uses_the_live_db_policy_end_to_end():
    """Full-stack proof: detect_and_open_incident() (the real code path
    the poller calls) must produce an Incident whose response_due_at
    actually reflects a changed sla_rules row, not the in-code default."""
    from datetime import datetime

    from app.ingestion import collector
    from app.models.db import Incident, Service

    db = _make_session()
    service = Service(name="payments-service", base_url="http://payments-service:8000")
    db.add(service)
    db.add(SLARule(severity=Severity.CRITICAL, response_minutes=2, resolution_minutes=10))
    db.commit()
    db.refresh(service)

    metrics = {"error_rate": 0.0, "latency_p95": 0.0, "availability": 0.0}  # forces CRITICAL
    before = datetime.utcnow()
    incident = collector.detect_and_open_incident(db, service, metrics)

    assert incident is not None
    assert incident.severity == Severity.CRITICAL
    # response_due_at must be ~2 minutes after opened_at (the DB value),
    # not 15 minutes (DEFAULT_SLA_MINUTES[CRITICAL]["response"]).
    delta = incident.response_due_at - incident.opened_at
    assert delta == timedelta(minutes=2)
    assert incident.resolution_due_at - incident.opened_at == timedelta(minutes=10)
