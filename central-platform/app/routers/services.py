from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.ingestion.collector import check_recovery, detect_and_open_incident, ingest_logs, poll_service
from app.models.db import MetricSample, Service, User

router = APIRouter(prefix="/services", tags=["services"])


class ServiceOut(BaseModel):
    id: int
    name: str
    base_url: str
    is_active: bool

    class Config:
        from_attributes = True


@router.get("", response_model=list[ServiceOut])
def list_services(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(Service).all()


@router.get("/{service_id}/metrics")
def service_metrics(
    service_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(MetricSample)
        .filter(MetricSample.service_id == service_id)
        .order_by(MetricSample.timestamp.desc())
        .limit(limit)
        .all()
    )
    return [
        {"metric_name": r.metric_name, "value": r.value, "timestamp": r.timestamp}
        for r in rows
    ]


@router.post("/{service_id}/poll-now")
def poll_now(service_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Manual trigger for the collector (normally run by the background
    scheduler in main.py) - useful for demos and for the failure-injection
    drills where you want an immediate read instead of waiting for the
    next poll interval.

    BUG-001 (found during the failure-injection drill, see docs/bug_reports.md):
    this originally called poll_service() only, which stores metric samples
    but never evaluates thresholds - so manually polling never actually
    opened an incident, only the background loop did. Fixed by also calling
    detect_and_open_incident() here, matching what the background loop does.

    Also runs log ingestion and recovery detection so a manual poll is a
    complete substitute for waiting out the background interval, not a
    partial one - see app/ingestion/collector.py::poll_all_services for
    the equivalent scheduled version.
    """
    service = db.get(Service, service_id)
    if not service:
        return {"error": "service not found"}
    metrics = poll_service(db, service)
    ingest_logs(db, service)
    recovered = check_recovery(db, service, metrics)
    incident = detect_and_open_incident(db, service, metrics)
    return {
        "service": service.name,
        "metrics": metrics,
        "polled_at": datetime.utcnow(),
        "incident_recovered": {"id": recovered.id, "title": recovered.title} if recovered else None,
        "incident_opened": {"id": incident.id, "title": incident.title, "severity": incident.severity.value}
        if incident else None,
    }
