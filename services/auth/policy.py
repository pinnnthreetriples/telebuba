"""Auth policy — credential verification, session issue/resolve, admin seeding.

Pure business logic over ``core.auth`` (hash/JWT) + the users repo. Returns
Pydantic models; no FastAPI. ``api/`` calls these; it never imports ``core.auth``.
"""

from __future__ import annotations

import uuid

from core import auth as core_auth
from core.config import settings
from core.logging import log_event
from core.repositories.users import (
    bump_token_version,
    count_users,
    create_user,
    get_user_by_id,
    get_user_by_username,
    set_password_hash,
)
from schemas.auth import LoginRequest, UserRead, UserRecord
from services.auth import _argon2

# Verifying against a throwaway hash when the user does not exist equalizes login
# timing, so a caller cannot enumerate usernames by response time.
_DUMMY_HASH = core_auth.hash_password("timing-equalizer-not-a-real-password")


async def authenticate(credentials: LoginRequest) -> UserRead | None:
    record = await get_user_by_username(credentials.username)
    if record is None:
        await _argon2.verify_password(credentials.password, _DUMMY_HASH)
        return None
    if not await _argon2.verify_password(credentials.password, record.password_hash):
        return None
    return record.to_read()


async def resolve_user(token: str) -> UserRead | None:
    claims = core_auth.decode_session_claims(token)
    if claims is None:
        return None
    record = await get_user_by_id(claims.sub)
    if record is None or record.token_version != claims.ver:
        # A stale ``ver`` means the token was revoked (logout bumped the counter).
        return None
    return record.to_read()


async def resolve_and_refresh(token: str) -> tuple[UserRead, str] | None:
    """The token's user and a sliding-TTL replacement stamped with the version just checked.

    Compare-and-issue: the new token carries the ``ver`` read together with the user, not
    a second read, so a logout between "resolve" and "issue" cannot yield a fresh token
    valid after the revocation (it is stale on arrival). The original login time rides
    along unchanged, so sliding never extends the absolute session lifetime.
    """
    claims = core_auth.decode_session_claims(token)
    if claims is None:
        return None
    record = await get_user_by_id(claims.sub)
    if record is None or record.token_version != claims.ver:
        return None
    refreshed = core_auth.encode_session_token(
        record.id,
        record.token_version,
        auth_time=claims.auth_time,
    )
    return record.to_read(), refreshed


async def issue_session_token(user_id: str) -> str:
    """Mint a session token stamped with the user's current revocation version."""
    record = await get_user_by_id(user_id)
    token_version = 0 if record is None else record.token_version
    return core_auth.encode_session_token(user_id, token_version)


async def revoke_sessions(user_id: str) -> None:
    """Invalidate all of a user's outstanding tokens by bumping their version (logout)."""
    await bump_token_version(user_id)


async def seed_admin_if_empty() -> None:
    """Create the first admin from ``settings.auth`` when no users exist (no public signup).

    When users already exist, the configured admin's password is kept in step with
    ``AUTH__ADMIN_PASSWORD`` (see :func:`_sync_admin_password`).
    """
    # A blank or whitespace-only password counts as "not configured".
    if not (settings.auth.admin_username and settings.auth.admin_password.strip()):
        return
    if await count_users() > 0:
        await _sync_admin_password()
        return
    record = UserRecord(
        id=uuid.uuid4().hex,
        username=settings.auth.admin_username,
        password_hash=await _argon2.hash_password(settings.auth.admin_password),
        role="admin",
    )
    await create_user(record)
    await log_event("INFO", "auth_admin_seeded", extra={"username": record.username})


async def _sync_admin_password() -> None:
    """Adopt a changed ``AUTH__ADMIN_PASSWORD``: new hash stored, every session revoked.

    Before this, editing the password in ``.env`` and restarting did nothing: the seed
    only ran on an empty table, so the old password kept working. Only the configured
    admin is touched, and only when the configured password no longer verifies, so a
    normal restart costs one hash check and writes nothing. The journal row carries the
    username only, never the password or the hash.
    """
    record = await get_user_by_username(settings.auth.admin_username)
    if record is None:
        return
    if await _argon2.verify_password(settings.auth.admin_password, record.password_hash):
        return
    await set_password_hash(record.id, await _argon2.hash_password(settings.auth.admin_password))
    await bump_token_version(record.id)
    await log_event("WARNING", "admin_password_changed", extra={"username": record.username})
