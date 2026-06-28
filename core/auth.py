"""Auth primitives — the only place passwords are hashed and JWTs are minted.

Per the auth ADR, no other module imports the JWT library or the password
hasher. ``services/auth`` composes these; ``api/`` sets the cookie.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

from core.config import settings

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    # InvalidHashError (a ValueError, raised on a malformed stored hash) is a
    # separate hierarchy from Argon2Error (wrong password), so catch both.
    try:
        return _hasher.verify(password_hash, password)
    except (Argon2Error, InvalidHashError):
        return False


def encode_session_token(user_id: str) -> str:
    """Mint a short-lived session JWT (sub=user_id). Sliding TTL is re-issued per request."""
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.auth.session_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.auth.secret, algorithm=settings.auth.algorithm)


def decode_session_token(token: str) -> str | None:
    """Return the user id from a valid token, or ``None`` if invalid/expired."""
    try:
        payload = jwt.decode(token, settings.auth.secret, algorithms=[settings.auth.algorithm])
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None
