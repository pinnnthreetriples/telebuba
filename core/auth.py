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
from schemas.auth import SessionClaims

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


def encode_session_token(user_id: str, token_version: int = 0) -> str:
    """Mint a short-lived session JWT (sub=user_id, ver=token_version).

    ``ver`` carries the user's current revocation counter; a token whose ``ver``
    no longer matches the stored one is rejected (logout bumps it). Sliding TTL
    is re-issued per request.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "ver": token_version,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.auth.session_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.auth.secret, algorithm=settings.auth.algorithm)


def decode_session_token(token: str) -> str | None:
    """Return the user id from a valid token, or ``None`` if invalid/expired."""
    claims = decode_session_claims(token)
    return None if claims is None else claims.sub


def decode_session_claims(token: str) -> SessionClaims | None:
    """Return the decoded sub + token version, or ``None`` if invalid/expired.

    A legacy token minted before the ``ver`` claim existed decodes with ``ver=0``
    (the initial ``token_version`` of every backfilled user), so it still resolves
    until the next logout bumps the counter.
    """
    try:
        payload = jwt.decode(token, settings.auth.secret, algorithms=[settings.auth.algorithm])
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    if not isinstance(sub, str):
        return None
    ver = payload.get("ver", 0)
    return SessionClaims(sub=sub, ver=ver if isinstance(ver, int) else 0)
