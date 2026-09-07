"""
SLA policy repository (Priority 1 fix).

AUDIT FINDING BEING FIXED: `sla_rules` existed as a real, seeded,
readable Postgres table, but `collector.py::detect_and_open_incident`
called `calculate_deadlines(opened_at, severity)` with no `sla_minutes`
argument, so every incident's deadlines were silently computed from the
hard-coded `DEFAULT_SLA_MINUTES` dict in app/sla/engine.py - changing a
row in `sla_rules` had zero effect on real behavior.

This module is the one place that gap is closed: it is the ONLY code
path that reads the live SLA policy for a severity, and every caller
that opens or backdates an incident goes through it instead of touching
DEFAULT_SLA_MINUTES directly. app/sla/engine.py itself stays pure/DB-free
(still independently unit-testable, per the project's own non-functional
requirement) - the DB access lives here, deliberately separated.
"""
import logging

from sqlalchemy.orm import Session

from app.models.db import SLARule
from app.sla.engine import DEFAULT_SLA_MINUTES, Severity, sla_minutes_from_rule

logger = logging.getLogger("central-platform.sla")


def get_sla_policy_for_severity(db: Session, severity) -> dict:
    """
    Fetches the live sla_rules row for `severity` from Postgres and
    returns it in the shape calculate_deadlines() expects. If (and only
    if) no row exists for that severity - a fresh DB before startup
    seeding has run, or a severity someone forgot to configure after
    editing the table - falls back to the in-code default and logs a
    warning, so incident creation never hard-fails, but the fallback is
    never the silent, permanent behavior it used to be.
    """
    severity = Severity(severity)
    rule = db.query(SLARule).filter(SLARule.severity == severity).first()
    if rule is None:
        logger.warning(
            "No sla_rules row found for severity=%s - falling back to the "
            "built-in default policy. This should only happen before "
            "startup seeding has run; if it persists, check the sla_rules "
            "table.",
            severity.value,
        )
        return {severity: DEFAULT_SLA_MINUTES[severity]}
    return sla_minutes_from_rule(rule)
