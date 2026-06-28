"""Auth policy — credential verification, session issue/resolve, admin seeding.

Pure business logic over ``core.auth`` (hash/JWT) + the users repo. Returns
Pydantic models; no FastAPI. ``api/`` calls these; it never imports ``core.auth``.
"""

from __future__ import annotations

import uuid

from core import auth as core_auth
from core.config import settings
from core.logging import log_event
from core.repositories.users import count_users, create_user, get_user_by_id, get_user_by_username
from schemas.auth import LoginRequest, UserRead, UserRecord

# Verifying against a throwaway hash when the user does not exist equalizes login
# timing, so a caller cannot enumerate usernames by response time.
_DUMMY_HASH = core_auth.hash_password("timing-equalizer-not-a-real-password")


async def authenticate(credentials: LoginRequest) -> UserRead | None:
    record = await get_user_by_username(credentials.username)
    if record is None:
        core_auth.verify_password(credentials.password, _DUMMY_HASH)
        return None
    if not core_auth.verify_password(credentials.password, record.password_hash):
        return None
    return record.to_read()


async def resolve_user(token: str) -> UserRead | None:
    user_id = core_auth.decode_session_token(token)
    if user_id is None:
        return None
    record = await get_user_by_id(user_id)
    return None if record is None else record.to_read()


def issue_session_token(user_id: str) -> str:
    return core_auth.encode_session_token(user_id)


async def seed_admin_if_empty() -> None:
    """Create the first admin from ``settings.auth`` when no users exist (no public signup)."""
    if not (settings.auth.admin_username and settings.auth.admin_password):
        return
    if await count_users() > 0:
        return
    record = UserRecord(
        id=uuid.uuid4().hex,
        username=settings.auth.admin_username,
        password_hash=core_auth.hash_password(settings.auth.admin_password),
        role="admin",
    )
    await create_user(record)
    await log_event("INFO", "auth_admin_seeded", extra={"username": record.username})
