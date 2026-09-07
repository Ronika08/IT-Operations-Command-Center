from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy.orm import Session

from app.conversion.csv_converter import convert
from app.core.db import get_db
from app.core.deps import require_roles
from app.models.db import AuditLog, Incident, IncidentStatus, Service, Severity, User, UserRole

router = APIRouter(prefix="/conversion", tags=["conversion"])


@router.post("/legacy-incidents")
async def convert_legacy_incidents(
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """
    Real end-to-end conversion: uploads a legacy CSV, validates/dedups it
    (app/conversion/csv_converter.py), then inserts every accepted row as
    a real Incident row (backfilled as already-resolved historical
    records, since these are legacy tickets, not live incidents).

    ADMIN-only: bulk-loading historical incident data is an administrative
    action, not a normal operator task - see docs/security.md.
    """
    raw = (await file.read()).decode("utf-8")
    report = convert(raw)

    inserted = 0
    for record in report.accepted_records:
        service = db.query(Service).filter(Service.name == record["service"]).first()
        if not service:
            continue  # unknown service name - counted separately below
        db.add(Incident(
            service_id=service.id,
            title=record["title"],
            severity=Severity(record["severity"]),
            status=IncidentStatus.RESOLVED,
            opened_at=record["opened_at"],
            resolved_at=record["opened_at"],
            response_due_at=record["opened_at"],
            resolution_due_at=record["opened_at"],
        ))
        inserted += 1

    db.add(AuditLog(
        user_id=current_user.id,
        action="legacy_csv_import",
        target_type="incident",
        target_id=None,
        details=f"{current_user.username} imported {inserted}/{report.total_rows} legacy rows from '{file.filename}'",
    ))
    db.commit()

    return {
        "total_rows": report.total_rows,
        "accepted": report.accepted,
        "rejected": report.rejected,
        "deduped": report.deduped,
        "inserted_into_db": inserted,
        "skipped_unknown_service": report.accepted - inserted,
        "log": report.as_log_text(),
    }
