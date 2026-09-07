"""
Authentication API (Priority 4).

A deliberately small auth surface: one login endpoint that checks a
hashed password against the real `users` table and issues a JWT, plus a
/me endpoint the frontend uses to show "logged in as ...". There is no
registration endpoint - users are seeded at startup (see
main.py::seed_reference_data) because this project's brief is a
controlled interview demo, not a self-service product.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.security import create_access_token, verify_password
from app.models.db import User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str
    role: str
    expires_in_minutes: int


class CurrentUserOut(BaseModel):
    user_id: int
    username: str
    role: str


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    from app.core.config import get_settings

    settings = get_settings()

    user = db.query(User).filter(User.username == payload.username).first()
    # Constant-shape response whether the username exists or not - avoids
    # confirming valid usernames to an attacker via response differences.
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid username or password")

    token = create_access_token(user.id, user.username, user.role.value)
    return LoginResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
        role=user.role.value,
        expires_in_minutes=settings.JWT_EXPIRE_MINUTES,
    )


@router.get("/me", response_model=CurrentUserOut)
def me(current_user: User = Depends(get_current_user)):
    return CurrentUserOut(user_id=current_user.id, username=current_user.username, role=current_user.role.value)
