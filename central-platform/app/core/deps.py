"""
Authentication/authorization FastAPI dependencies (Priority 4).

    Authorization: Bearer <token>
        -> decode_access_token()
        -> load the real User row (so a deleted/disabled user is rejected
           even with a still-valid, unexpired token)
        -> current_user available to the route
        -> require_roles(...) gates state-changing routes by role

This is deliberately a small, explicit dependency chain rather than a
general-purpose permission framework - see docs/security.md for why.
"""
import jwt as pyjwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import decode_access_token
from app.models.db import User, UserRole


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing or malformed Authorization header")

    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_access_token(token)
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid or expired token")

    user = db.get(User, claims.user_id)
    if not user:
        raise HTTPException(status_code=401, detail="user for this token no longer exists")
    return user


def require_roles(*allowed_roles: UserRole):
    """Returns a FastAPI dependency that accepts any of the given roles.
    Usage: Depends(require_roles(UserRole.ADMIN, UserRole.INCIDENT_MANAGER))."""

    def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            allowed = ", ".join(r.value for r in allowed_roles)
            raise HTTPException(
                status_code=403,
                detail=f"role '{current_user.role.value}' is not permitted - requires one of: {allowed}",
            )
        return current_user

    return _check
