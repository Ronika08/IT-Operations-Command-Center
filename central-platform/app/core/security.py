"""
Password hashing + JWT issuance/verification.

Scope note (deliberate, not an oversight): this uses the stdlib's
`hashlib.pbkdf2_hmac` for password hashing rather than adding
bcrypt/argon2 as a new dependency. PBKDF2-SHA256 with a per-user random
salt and 260k iterations (Django's own current default) is a real,
non-reversible, salted hash - a legitimate choice for a project whose
explicit brief is "do not add technologies just to make it look
impressive." bcrypt/argon2id would be the production upgrade (see
docs/security.md, "Production evolution").

JWT uses PyJWT (already a small, standard dependency) with HS256 and a
server-side secret from `settings.JWT_SECRET_KEY`. Tokens carry only
non-sensitive claims (user id, username, role, expiry) - never the
password or its hash.
"""
import hashlib
import hmac
import os
import time
from dataclasses import dataclass

import jwt

from app.core.config import get_settings

settings = get_settings()

_PBKDF2_ALGORITHM = "sha256"
_PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    """Returns 'pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>'."""
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac(_PBKDF2_ALGORITHM, password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time comparison against a hash produced by hash_password().
    Never raises on a malformed stored_hash - treats it as a non-match,
    since a corrupt/legacy hash should never be treated as "no password
    required"."""
    try:
        algo, iterations_str, salt_hex, hash_hex = stored_hash.split("$")
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False

    candidate = hashlib.pbkdf2_hmac(_PBKDF2_ALGORITHM, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


@dataclass
class TokenPayload:
    user_id: int
    username: str
    role: str


def create_access_token(user_id: int, username: str, role: str) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + settings.JWT_EXPIRE_MINUTES * 60,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> TokenPayload:
    """Raises jwt.PyJWTError (caught by the caller) on any invalid,
    expired, or tampered token - never returns a partially-trusted
    payload."""
    payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    return TokenPayload(user_id=int(payload["sub"]), username=payload["username"], role=payload["role"])
