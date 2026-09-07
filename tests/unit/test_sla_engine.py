"""
Unit tests for app/sla/engine.py.

Pure logic, no DB/network - run with: pytest tests/unit/test_sla_engine.py -v
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "central-platform"))

from app.sla.engine import (  # noqa: E402
    Severity,
    calculate_deadlines,
    is_resolution_breached,
    is_response_breached,
    time_remaining_seconds,
)


def test_calculate_deadlines_critical():
    opened = datetime(2026, 1, 1, 10, 0, 0)
    deadlines = calculate_deadlines(opened, Severity.CRITICAL)
    assert deadlines.response_due_at == opened + timedelta(minutes=15)
    assert deadlines.resolution_due_at == opened + timedelta(minutes=240)


def test_calculate_deadlines_low():
    opened = datetime(2026, 1, 1, 10, 0, 0)
    deadlines = calculate_deadlines(opened, Severity.LOW)
    assert deadlines.response_due_at == opened + timedelta(minutes=480)
    assert deadlines.resolution_due_at == opened + timedelta(minutes=4320)


def test_response_not_breached_when_acknowledged_in_time():
    due = datetime(2026, 1, 1, 10, 15, 0)
    ack = datetime(2026, 1, 1, 10, 10, 0)
    assert is_response_breached(due, ack) is False


def test_response_breached_when_acknowledged_late():
    due = datetime(2026, 1, 1, 10, 15, 0)
    ack = datetime(2026, 1, 1, 10, 20, 0)
    assert is_response_breached(due, ack) is True


def test_response_breached_when_not_acknowledged_and_now_past_due():
    due = datetime(2026, 1, 1, 10, 15, 0)
    now = datetime(2026, 1, 1, 10, 30, 0)
    assert is_response_breached(due, None, now=now) is True


def test_response_not_breached_when_not_acknowledged_but_still_within_window():
    due = datetime(2026, 1, 1, 10, 15, 0)
    now = datetime(2026, 1, 1, 10, 5, 0)
    assert is_response_breached(due, None, now=now) is False


def test_resolution_breach_mirrors_response_logic():
    due = datetime(2026, 1, 1, 14, 0, 0)
    resolved_late = datetime(2026, 1, 1, 15, 0, 0)
    resolved_on_time = datetime(2026, 1, 1, 13, 0, 0)
    assert is_resolution_breached(due, resolved_late) is True
    assert is_resolution_breached(due, resolved_on_time) is False


def test_time_remaining_seconds_negative_when_overdue():
    due = datetime(2026, 1, 1, 10, 0, 0)
    now = datetime(2026, 1, 1, 10, 5, 0)
    remaining = time_remaining_seconds(due, now=now)
    assert remaining == -300.0


def test_time_remaining_seconds_positive_when_within_window():
    due = datetime(2026, 1, 1, 10, 10, 0)
    now = datetime(2026, 1, 1, 10, 5, 0)
    remaining = time_remaining_seconds(due, now=now)
    assert remaining == 300.0


# ---------------------------------------------------------------------------
# Boundary-condition edge cases (added during the audit - the original
# suite covered clearly-before/clearly-after cases but not the exact
# instant of breach, which is where off-by-one errors in a `>` vs `>=`
# choice actually surface).
# ---------------------------------------------------------------------------
def test_response_exactly_at_deadline_is_not_yet_breached():
    """The engine uses a strict '>' comparison, so the exact due instant
    itself is still on-time, not breached - this pins that intentional
    boundary choice down with a test instead of leaving it implicit."""
    due = datetime(2026, 1, 1, 10, 15, 0)
    assert is_response_breached(due, None, now=due) is False
    assert is_response_breached(due, due) is False


def test_resolution_exactly_at_deadline_is_not_yet_breached():
    due = datetime(2026, 1, 1, 14, 0, 0)
    assert is_resolution_breached(due, None, now=due) is False
    assert is_resolution_breached(due, due) is False


def test_response_breached_by_one_second_past_deadline():
    due = datetime(2026, 1, 1, 10, 15, 0)
    one_second_late = datetime(2026, 1, 1, 10, 15, 1)
    assert is_response_breached(due, None, now=one_second_late) is True
    assert is_response_breached(due, one_second_late) is True


def test_time_remaining_seconds_zero_at_exact_deadline():
    due = datetime(2026, 1, 1, 10, 0, 0)
    assert time_remaining_seconds(due, now=due) == 0.0


def test_already_resolved_incident_does_not_become_breached_by_elapsed_time():
    """Once resolved on time, an incident's resolution breach status must
    stay False even if 'now' (evaluated later, e.g. on a dashboard
    refresh) is long past the original deadline - the breach check must
    be anchored to resolved_at, not to wall-clock 'now', once resolved."""
    due = datetime(2026, 1, 1, 14, 0, 0)
    resolved_on_time = datetime(2026, 1, 1, 13, 30, 0)
    much_later = datetime(2026, 1, 5, 9, 0, 0)
    assert is_resolution_breached(due, resolved_on_time, now=much_later) is False
