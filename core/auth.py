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
_AUTH_TIME_FUTURE_SKEW_SECONDS = 300


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    # InvalidHashError (a ValueError, raised on a malformed stored hash) is a
    # separate hierarchy from Argon2Error (wrong password), so catch both.
    try:
        return _hasher.verify(password_hash, password)
    except (Argon2Error, InvalidHashError):
        return False


def encode_session_token(
    user_id: str,
    token_version: int = 0,
    auth_time: int | None = None,
) -> str:
    """Mint a short-lived session JWT (sub=user_id, ver=token_version).

    ``ver`` carries the user's current revocation counter; a token whose ``ver``
    no longer matches the stored one is rejected (logout bumps it). Sliding TTL
    is re-issued per request; ``auth_time`` is the original login time, which a
    re-issue passes through unchanged (``None`` = logging in now) so the absolute
    lifetime (``session_max_days``) cannot be slid past.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "ver": token_version,
        "auth_time": int(now.timestamp()) if auth_time is None else auth_time,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.auth.session_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.auth.secret, algorithm=settings.auth.algorithm)


def decode_session_token(token: str) -> str | None:
    """Return the user id from a valid token, or ``None`` if invalid/expired."""
    claims = decode_session_claims(token)
    return None if claims is None else claims.sub


def _auth_time_in_range(auth_time: int) -> bool:
    """Not older than ``session_max_days``, not more than five minutes ahead of now.

    Plain integer seconds: an extreme signed value cannot overflow a datetime conversion.
    """
    age = int(datetime.now(UTC).timestamp()) - auth_time
    return (
        -_AUTH_TIME_FUTURE_SKEW_SECONDS
        <= age
        <= timedelta(days=settings.auth.session_max_days).total_seconds()
    )


def decode_session_claims(token: str) -> SessionClaims | None:
    """Return the decoded sub + token version, or ``None`` if invalid/expired.

    A legacy token minted before the ``ver`` claim existed decodes with ``ver=0``
    (the initial ``token_version`` of every backfilled user), so it still resolves
    until the next logout bumps the counter.

    A token without ``auth_time`` (minted before the claim existed) counts as logged
    in at its ``iat``; one older than ``session_max_days`` is rejected whatever its
    ``exp`` says, because a sliding re-issue keeps pushing ``exp`` forward. One stamped
    more than five minutes in the future (a wrong clock at login) is rejected too: it
    would never age out.

    When no secret is configured, no token is ever valid — mirroring the issuance
    guard so both paths agree "no secret => no auth" (defense in depth).
    """
    if not settings.auth.secret:
        return None
    try:
        payload = jwt.decode(token, settings.auth.secret, algorithms=[settings.auth.algorithm])
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    if not isinstance(sub, str):
        return None
    ver = payload.get("ver", 0)
    auth_time = payload.get("auth_time", payload.get("iat"))
    if not isinstance(auth_time, int) or isinstance(auth_time, bool):
        return None
    if not _auth_time_in_range(auth_time):
        return None
    return SessionClaims(sub=sub, ver=ver if isinstance(ver, int) else 0, auth_time=auth_time)
