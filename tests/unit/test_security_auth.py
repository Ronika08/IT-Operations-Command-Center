"""
Unit tests for app/core/security.py - password hashing and JWT
issuance/verification. Pure logic, no DB/network.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "central-platform"))
os.environ.setdefault("JWT_SECRET_KEY", "unit-test-secret")

import jwt as pyjwt  # noqa: E402

from app.core.security import (  # noqa: E402
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_password_is_not_plaintext():
    hashed = hash_password("correct horse battery staple")
    assert "correct horse battery staple" not in hashed
    assert hashed.startswith("pbkdf2_sha256$")


def test_verify_password_accepts_correct_password():
    hashed = hash_password("hunter2")
    assert verify_password("hunter2", hashed) is True


def test_verify_password_rejects_wrong_password():
    hashed = hash_password("hunter2")
    assert verify_password("wrong-password", hashed) is False


def test_two_hashes_of_same_password_differ_due_to_random_salt():
    a = hash_password("same-password")
    b = hash_password("same-password")
    assert a != b
    assert verify_password("same-password", a)
    assert verify_password("same-password", b)


def test_verify_password_never_raises_on_malformed_stored_hash():
    assert verify_password("anything", "not-a-real-hash") is False
    assert verify_password("anything", "") is False


def test_create_and_decode_access_token_round_trips():
    token = create_access_token(user_id=42, username="rca_reviewer", role="rca_reviewer")
    claims = decode_access_token(token)
    assert claims.user_id == 42
    assert claims.username == "rca_reviewer"
    assert claims.role == "rca_reviewer"


def test_decode_rejects_tampered_token():
    token = create_access_token(user_id=1, username="admin", role="admin")
    tampered = token[:-2] + ("aa" if token[-2:] != "aa" else "bb")
    try:
        decode_access_token(tampered)
        assert False, "expected a JWT error for a tampered token"
    except pyjwt.PyJWTError:
        pass


def test_decode_rejects_expired_token(monkeypatch):
    """Forces JWT_EXPIRE_MINUTES effectively negative by issuing with a
    monkeypatched settings object, then confirms decode fails."""
    import app.core.security as security_module

    original_expire = security_module.settings.JWT_EXPIRE_MINUTES
    security_module.settings.JWT_EXPIRE_MINUTES = -1  # already expired the instant it's issued
    try:
        token = create_access_token(user_id=1, username="admin", role="admin")
    finally:
        security_module.settings.JWT_EXPIRE_MINUTES = original_expire

    try:
        decode_access_token(token)
        assert False, "expected an expired-token error"
    except pyjwt.ExpiredSignatureError:
        pass
