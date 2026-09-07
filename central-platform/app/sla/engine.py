"""
SLA Engine
----------
Pure, dependency-free calculation logic (deliberately kept out of the
routers/DB layer so it can be unit tested in isolation - see
tests/unit/test_sla_engine.py).

Business rule this encodes: every severity level has a response deadline
(time to acknowledge) and a resolution deadline (time to fully resolve),
measured from the real `opened_at` timestamp of the incident. A breach is
a fact derived from real clock time, not a flag someone sets by hand.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# Default SLA policy (minutes). Seeded into sla_rules table; can be
# overridden per-deployment via the DB without a code change.
DEFAULT_SLA_MINUTES = {
    Severity.CRITICAL: {"response": 15, "resolution": 240},   # 4h resolve
    Severity.HIGH: {"response": 30, "resolution": 480},       # 8h resolve
    Severity.MEDIUM: {"response": 120, "resolution": 1440},   # 24h resolve
    Severity.LOW: {"response": 480, "resolution": 4320},      # 72h resolve
}


@dataclass
class SLADeadlines:
    response_due_at: datetime
    resolution_due_at: datetime


def sla_minutes_from_rule(rule) -> dict:
    """
    Adapts a DB SLARule row (or anything with .severity/.response_minutes/
    .resolution_minutes attributes - e.g. a test double) into the single-
    severity {Severity: {"response": x, "resolution": y}} shape
    calculate_deadlines() expects.

    Kept here, in the pure/DB-free engine module, rather than in
    sla/repository.py, so it stays unit-testable with a plain object and
    no database - the actual Postgres query lives in sla/repository.py.
    """
    severity = Severity(rule.severity)
    return {severity: {"response": rule.response_minutes, "resolution": rule.resolution_minutes}}


def calculate_deadlines(opened_at: datetime, severity: Severity,
                         sla_minutes: dict = None) -> SLADeadlines:
    """Given when an incident opened and its severity, compute the two
    real deadlines it must be measured against."""
    policy = sla_minutes or DEFAULT_SLA_MINUTES
    rule = policy[Severity(severity)]
    return SLADeadlines(
        response_due_at=opened_at + timedelta(minutes=rule["response"]),
        resolution_due_at=opened_at + timedelta(minutes=rule["resolution"]),
    )


def is_response_breached(response_due_at: datetime, acknowledged_at: datetime | None,
                          now: datetime | None = None) -> bool:
    """
    Response SLA is breached if:
      - the incident was acknowledged AFTER the deadline, OR
      - it still hasn't been acknowledged and 'now' is already past the deadline.
    """
    now = now or datetime.utcnow()
    if acknowledged_at is not None:
        return acknowledged_at > response_due_at
    return now > response_due_at


def is_resolution_breached(resolution_due_at: datetime, resolved_at: datetime | None,
                            now: datetime | None = None) -> bool:
    now = now or datetime.utcnow()
    if resolved_at is not None:
        return resolved_at > resolution_due_at
    return now > resolution_due_at


def time_remaining_seconds(due_at: datetime, now: datetime | None = None) -> float:
    """Negative value means already overdue by that many seconds."""
    now = now or datetime.utcnow()
    return (due_at - now).total_seconds()
