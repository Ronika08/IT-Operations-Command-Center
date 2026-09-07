"""
Read-only Audit Trail API.

Exposes the AuditLog rows that already get written by
app/routers/incidents.py (acknowledge/resolve/verify_rca) - this file
adds NO new writes, no new audit-logging behavior, and does not import
or modify anything in incidents.py. It only queries the existing
`audit_logs` table via the existing AuditLog model and the existing
get_db session dependency, matching the read-only style already used by
app/routers/sla.py (list_rules/sla_summary).
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.models.db import AuditLog, User

router = APIRouter(prefix="/audit-logs", tags=["audit"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class AuditLogOut(BaseModel):
    id: int
    user_id: Optional[int]
    action: str
    target_type: str
    target_id: Optional[int]
    details: Optional[str]
    timestamp: str

    class Config:
        from_attributes = True


@router.get("", response_model=list[AuditLogOut])
def list_audit_logs(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Recent audit records, most recent first. `limit` defaults to 50
    and is capped at 200 - both enforced by FastAPI/Pydantic's `Query`
    validation (an out-of-range value returns 422, not a silently
    clamped result)."""
    rows = (
        db.query(AuditLog)
        .order_by(AuditLog.timestamp.desc())
        .limit(limit)
        .all()
    )
    return [
        AuditLogOut(
            id=row.id,
            user_id=row.user_id,  # None is valid and passed through as-is (Optional[int])
            action=row.action,
            target_type=row.target_type,
            target_id=row.target_id,
            details=row.details,
            timestamp=row.timestamp.isoformat(),
        )
        for row in rows
    ]
