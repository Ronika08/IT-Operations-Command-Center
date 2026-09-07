"""
Incidents API
--------------
CRUD + lifecycle actions (acknowledge/resolve) + RCA trigger for incidents.
Every state-changing action writes a real AuditLog row (see
app/models/db.py) so SLA-process adherence is queryable, not asserted.

Priority 4 (auth): acknowledge/resolve/RCA-verify are role-gated and the
acting user's real id (from the verified JWT) is what gets written to
AuditLog.user_id - callers can no longer just supply an arbitrary
user_id in the request body, which is the whole point of this change.
Read endpoints (list/get) require a valid login but are not role-
restricted - see docs/security.md for that design choice.

Priority 6 (pagination): GET /incidents now returns
{items, page, limit, total} instead of a bare list, and breach-flag
refreshes are batched into a single commit per request instead of one
commit per incident row.
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user, require_roles
from app.models.db import (
    AuditLog,
    Incident,
    IncidentStatus,
    LogEntry,
    MetricSample,
    Recommendation,
    User,
    UserRole,
)
from app.rca.ai_validator import validate_explanation
from app.rca.llm_explainer import explain as llm_explain
from app.rca.rules import analyze as rca_analyze
from app.sla import engine as sla_engine

router = APIRouter(prefix="/incidents", tags=["incidents"])

MAX_PAGE_LIMIT = 100
DEFAULT_PAGE_LIMIT = 20


class IncidentOut(BaseModel):
    id: int
    service_id: int
    title: str
    severity: str
    status: str
    opened_at: datetime
    acknowledged_at: datetime | None
    recovered_at: datetime | None
    resolved_at: datetime | None
    response_due_at: datetime
    resolution_due_at: datetime
    response_breached: bool
    resolution_breached: bool
    trigger_metric: str | None
    trigger_value: float | None
    trigger_threshold: float | None

    class Config:
        from_attributes = True


class PaginatedIncidents(BaseModel):
    items: list[IncidentOut]
    page: int
    limit: int
    total: int


class AckRequest(BaseModel):
    note: str | None = None


class ResolveRequest(BaseModel):
    note: str | None = None


class VerifyRequest(BaseModel):
    verdict: str  # "accepted" | "rejected" | "modified"
    note: str | None = None


class _ValidationResultShim:
    """Tiny local shim so the "reuse a previous successful AI
    recommendation" branch in run_rca() can carry that recommendation's
    already-stored validation result without re-running
    validate_explanation() on text that was never (re)generated."""

    def __init__(self, status, reason):
        self.status = status
        self.reason = reason


def _refresh_breach_flags(incidents: list[Incident], db: Session) -> None:
    """
    Recomputes response/resolution breach flags for the given incidents
    in memory, and commits ONCE for the whole batch (not once per
    incident, per row) - the fix for the write-amplification finding
    from the audit, where every GET /incidents refreshed and committed
    each row individually.
    """
    changed = False
    for incident in incidents:
        new_response_breached = sla_engine.is_response_breached(
            incident.response_due_at, incident.acknowledged_at,
        )
        new_resolution_breached = sla_engine.is_resolution_breached(
            incident.resolution_due_at, incident.resolved_at,
        )
        if new_response_breached != incident.response_breached:
            incident.response_breached = new_response_breached
            changed = True
        if new_resolution_breached != incident.resolution_breached:
            incident.resolution_breached = new_resolution_breached
            changed = True
        if new_resolution_breached and incident.status not in (
            IncidentStatus.RESOLVED, IncidentStatus.BREACHED,
        ):
            # BREACHED can override OPEN/ACKNOWLEDGED/RECOVERED alike - even
            # a service that recovered on its own still breaches its
            # resolution SLA if a human never formally closes it in time.
            incident.status = IncidentStatus.BREACHED
            changed = True

    if changed:
        db.commit()
        for incident in incidents:
            db.refresh(incident)


@router.get("", response_model=PaginatedIncidents)
def list_incidents(
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Incident)
    if status:
        q = q.filter(Incident.status == status)

    total = q.count()
    incidents = (
        q.order_by(Incident.opened_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    _refresh_breach_flags(incidents, db)

    return PaginatedIncidents(items=incidents, page=page, limit=limit, total=total)


@router.get("/{incident_id}", response_model=IncidentOut)
def get_incident(
    incident_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404, "incident not found")

    _refresh_breach_flags([incident], db)
    return incident


@router.post("/{incident_id}/acknowledge", response_model=IncidentOut)
def acknowledge(
    incident_id: int,
    payload: AckRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_roles(UserRole.IT_SUPPORT, UserRole.INCIDENT_MANAGER, UserRole.ADMIN)
    ),
):
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404, "incident not found")
    if incident.acknowledged_at is not None:
        raise HTTPException(400, "already acknowledged")

    incident.acknowledged_at = datetime.utcnow()
    incident.status = IncidentStatus.ACKNOWLEDGED

    db.add(AuditLog(
        user_id=current_user.id,
        action="acknowledge",
        target_type="incident",
        target_id=incident.id,
        details=payload.note or f"incident acknowledged by {current_user.username}",
    ))
    db.commit()
    db.refresh(incident)

    _refresh_breach_flags([incident], db)
    return incident


@router.post("/{incident_id}/resolve", response_model=IncidentOut)
def resolve(
    incident_id: int,
    payload: ResolveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_roles(UserRole.INCIDENT_MANAGER, UserRole.ADMIN)
    ),
):
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404, "incident not found")
    if incident.resolved_at is not None:
        raise HTTPException(400, "already resolved")

    incident.resolved_at = datetime.utcnow()
    incident.status = IncidentStatus.RESOLVED

    db.add(AuditLog(
        user_id=current_user.id,
        action="resolve",
        target_type="incident",
        target_id=incident.id,
        details=payload.note or f"incident resolved by {current_user.username}",
    ))
    db.commit()
    db.refresh(incident)

    _refresh_breach_flags([incident], db)
    return incident


def _get_previous_successful_ai_recommendation(
    incident_id: int,
    current_rec_id: int,
    current_summary: str,
    db: Session,
) -> Recommendation | None:
    """
    Find the most recent previously successful AI recommendation for
    the same incident and same deterministic RCA summary.

    This prevents a failed Gemini retry from hiding an already-successful
    explanation, while avoiding reuse of an explanation for a different
    deterministic RCA result.
    """
    return (
        db.query(Recommendation)
        .filter(
            Recommendation.incident_id == incident_id,
            Recommendation.id != current_rec_id,
            Recommendation.ai_confidence_note.like("%llm_used=True%"),
            Recommendation.rule_based_summary == current_summary,
        )
        .order_by(Recommendation.created_at.desc(), Recommendation.id.desc())
        .first()
    )


@router.post("/{incident_id}/rca")
def run_rca(
    incident_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Runs the rule-based RCA engine against the incident's real, correlated
    metric samples + log lines, asks the LLM to explain that structured
    result, then runs the deterministic AI-explanation validator
    (Priority 5) against the LLM's own text. Stores all three outputs,
    unverified, for a human to review.

    Any authenticated role may run RCA - it only produces a suggestion
    for review, never a final decision, so it is treated like a read
    operation, not a state-changing one. Verifying/accepting a
    recommendation is the RCA_REVIEWER-gated action below.
    """
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404, "incident not found")

    window_start = incident.opened_at

    recent_metrics = (
        db.query(MetricSample)
        .filter(MetricSample.service_id == incident.service_id, MetricSample.timestamp >= window_start)
        .all()
    )
    recent_logs = (
        db.query(LogEntry)
        .filter(LogEntry.service_id == incident.service_id, LogEntry.timestamp >= window_start)
        .all()
    )

    metrics_dict = {}
    for m in recent_metrics:
        if m.metric_name == "availability":
            metrics_dict[m.metric_name] = min(metrics_dict.get(m.metric_name, 1.0), m.value)
        elif m.metric_name in ("error_rate", "latency_p95"):
            metrics_dict[m.metric_name] = max(metrics_dict.get(m.metric_name, 0.0), m.value)
        else:
            metrics_dict[m.metric_name] = m.value

    if incident.trigger_metric and incident.trigger_value is not None:
        metrics_dict[incident.trigger_metric] = incident.trigger_value

    log_messages = [l.message for l in recent_logs]

    rca_result = rca_analyze(metrics_dict, log_messages)
    llm_result = llm_explain(rca_result)
    validation = validate_explanation(llm_result.text, rca_result, llm_result.used_llm)

    rec = Recommendation(
        incident_id=incident.id,
        rule_based_summary=rca_result.summary,
        evidence_json=json.dumps(
            [{"kind": e.kind, "description": e.description, "weight": e.weight} for e in rca_result.evidence]
        ),
        ai_explanation=llm_result.text,
        ai_confidence_note=f"rule_confidence={rca_result.confidence}; llm_used={llm_result.used_llm}",
        ai_validation_status=validation.status,
        ai_validation_reason=validation.reason,
        verified_by_human=False,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    # If the current Gemini request failed, look for an already-successful
    # AI explanation for the same deterministic RCA result.
    display_ai_explanation = llm_result.text
    display_used_llm = llm_result.used_llm
    display_recommendation_id = rec.id
    display_validation = validation

    if not llm_result.used_llm:
        previous_success = _get_previous_successful_ai_recommendation(
            incident_id=incident.id, current_rec_id=rec.id, current_summary=rca_result.summary, db=db,
        )
        if previous_success and previous_success.ai_explanation:
            display_ai_explanation = previous_success.ai_explanation
            display_used_llm = True
            display_recommendation_id = previous_success.id
            display_validation = _ValidationResultShim(
                previous_success.ai_validation_status, previous_success.ai_validation_reason,
            )

    return {
        "recommendation_id": display_recommendation_id,
        "root_cause_category": rca_result.root_cause_category,
        "confidence": rca_result.confidence,
        "rule_based_summary": rca_result.summary,
        "ai_explanation": display_ai_explanation,
        "used_llm": display_used_llm,
        "ai_validation_status": display_validation.status,
        "ai_validation_reason": display_validation.reason,
    }


@router.post("/recommendations/{recommendation_id}/verify")
def verify_recommendation(
    recommendation_id: int,
    payload: VerifyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.RCA_REVIEWER, UserRole.ADMIN)),
):
    """
    Human verification of an RCA recommendation. The AI/rule engine never
    verifies itself - only an RCA_REVIEWER or ADMIN may accept, reject,
    or modify it, and the action is attributed to the real authenticated
    user, not a caller-supplied id.
    """
    allowed_verdicts = {"accepted", "rejected", "modified"}
    if payload.verdict not in allowed_verdicts:
        raise HTTPException(400, "verdict must be accepted, rejected, or modified")

    recommendation = db.get(Recommendation, recommendation_id)
    if not recommendation:
        raise HTTPException(404, "recommendation not found")

    recommendation.verified_by_human = True
    recommendation.human_verdict = payload.verdict

    details = payload.note or f"human verdict: {payload.verdict} (by {current_user.username})"
    db.add(AuditLog(
        user_id=current_user.id,
        action="verify_rca",
        target_type="recommendation",
        target_id=recommendation.id,
        details=details,
    ))
    db.commit()
    db.refresh(recommendation)

    return {
        "recommendation_id": recommendation.id,
        "verified_by_human": recommendation.verified_by_human,
        "human_verdict": recommendation.human_verdict,
    }
