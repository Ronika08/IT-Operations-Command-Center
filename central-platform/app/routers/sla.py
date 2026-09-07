"""
SLA API.

GET /rules and GET /summary require any authenticated role (read-only).
PUT /rules/{severity} is the controlled admin API requested for Priority
1 - ADMIN only - so the "SLA policy is data-driven" claim has a real,
audited way to actually change that data, not just a read-only mirror.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user, require_roles
from app.models.db import AuditLog, Incident, IncidentStatus, SLARule, User, UserRole
from app.sla.engine import Severity

router = APIRouter(prefix="/sla", tags=["sla"])


class SLARuleOut(BaseModel):
    severity: str
    response_minutes: int
    resolution_minutes: int

    class Config:
        from_attributes = True


class SLARuleUpdate(BaseModel):
    response_minutes: int = Field(gt=0, le=10_080)      # up to 1 week
    resolution_minutes: int = Field(gt=0, le=43_200)     # up to 30 days


@router.get("/rules", response_model=list[SLARuleOut])
def list_rules(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(SLARule).all()


@router.put("/rules/{severity}", response_model=SLARuleOut)
def update_rule(
    severity: str,
    payload: SLARuleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """
    The controlled write path for SLA policy (Priority 1). Only an ADMIN
    may call this. Any incident opened AFTER this change picks up the new
    values via app/sla/repository.py::get_sla_policy_for_severity -
    incidents already open keep their originally-calculated deadlines
    (changing a deadline retroactively on an in-flight incident would
    make its SLA history meaningless).
    """
    try:
        severity_enum = Severity(severity)
    except ValueError:
        raise HTTPException(400, f"unknown severity '{severity}'")

    rule = db.query(SLARule).filter(SLARule.severity == severity_enum).first()
    if not rule:
        raise HTTPException(404, f"no sla_rules row exists for severity '{severity}' - seeding may not have run")

    old = f"response={rule.response_minutes}m, resolution={rule.resolution_minutes}m"
    rule.response_minutes = payload.response_minutes
    rule.resolution_minutes = payload.resolution_minutes
    new = f"response={rule.response_minutes}m, resolution={rule.resolution_minutes}m"

    db.add(AuditLog(
        user_id=current_user.id,
        action="sla_rule_updated",
        target_type="sla_rule",
        target_id=rule.id,
        details=f"{severity} SLA changed by {current_user.username}: {old} -> {new}",
    ))
    db.commit()
    db.refresh(rule)
    return rule


@router.get("/summary")
def sla_summary(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Aggregate SLA compliance across all incidents - the number an
    IT-ops manager would actually look at first."""
    total = db.query(Incident).count()
    breached = db.query(Incident).filter(
        (Incident.response_breached == True) | (Incident.resolution_breached == True)  # noqa: E712
    ).count()
    open_now = db.query(Incident).filter(Incident.status.in_(
        [IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED, IncidentStatus.RECOVERED]
    )).count()

    compliance_pct = round(100 * (1 - breached / total), 2) if total else 100.0
    return {
        "total_incidents": total,
        "breached_incidents": breached,
        "currently_open": open_now,
        "sla_compliance_pct": compliance_pct,
    }
