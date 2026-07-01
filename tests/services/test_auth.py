"""services/auth — authenticate, resolve, seed, and the login rate limiter."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core import auth as core_auth
from core.config import settings
from core.db import configure_database
from core.repositories.users import count_users, create_user, get_user_by_username
from schemas.auth import LoginRequest, UserRecord
from services import auth as auth_service

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.auth, "secret", "svc-test-secret-0123456789abcdef-pad")


async def _seed_user(username: str = "admin", password: str = "pw") -> None:
    await create_user(
        UserRecord(
            id="u1",
            username=username,
            password_hash=core_auth.hash_password(password),
            role="admin",
        ),
    )


@pytest.mark.asyncio
async def test_authenticate_accepts_valid_credentials() -> None:
    await _seed_user()
    user = await auth_service.authenticate(LoginRequest(username="admin", password="pw"))
    assert user is not None
    assert user.username == "admin"


@pytest.mark.asyncio
async def test_authenticate_rejects_wrong_password() -> None:
    await _seed_user()
    assert await auth_service.authenticate(LoginRequest(username="admin", password="x")) is None


@pytest.mark.asyncio
async def test_authenticate_rejects_unknown_user() -> None:
    assert await auth_service.authenticate(LoginRequest(username="ghost", password="x")) is None


@pytest.mark.asyncio
async def test_resolve_user_round_trips_a_session_token() -> None:
    await _seed_user()
    token = auth_service.issue_session_token("u1")
    resolved = await auth_service.resolve_user(token)
    assert resolved is not None
    assert resolved.id == "u1"


@pytest.mark.asyncio
async def test_resolve_user_rejects_a_bad_token() -> None:
    assert await auth_service.resolve_user("garbage") is None


@pytest.mark.asyncio
async def test_seed_admin_creates_the_first_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.auth, "admin_username", "root")
    monkeypatch.setattr(settings.auth, "admin_password", "rootpw")
    await auth_service.seed_admin_if_empty()
    assert await count_users() == 1
    assert await get_user_by_username("root") is not None


@pytest.mark.asyncio
async def test_seed_admin_is_a_noop_when_users_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    await _seed_user()
    monkeypatch.setattr(settings.auth, "admin_username", "root")
    monkeypatch.setattr(settings.auth, "admin_password", "rootpw")
    await auth_service.seed_admin_if_empty()
    assert await count_users() == 1
    assert await get_user_by_username("root") is None


@pytest.mark.asyncio
async def test_seed_admin_is_a_noop_without_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pin the seed inputs empty so the test is hermetic — it must not depend on a
    # developer's local .env (which may set AUTH__ADMIN_* for login).
    monkeypatch.setattr(settings.auth, "admin_username", "")
    monkeypatch.setattr(settings.auth, "admin_password", "")
    await auth_service.seed_admin_if_empty()
    assert await count_users() == 0


def test_rate_limiter_blocks_after_the_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.auth, "login_rate_limit_max_attempts", 2)
    monkeypatch.setattr(settings.auth, "login_rate_limit_window_seconds", 100.0)
    assert auth_service.check_login_rate_limit("ip-a", 1.0) is True
    assert auth_service.check_login_rate_limit("ip-a", 1.1) is True
    assert auth_service.check_login_rate_limit("ip-a", 1.2) is False
    # A later attempt outside the window is allowed again.
    assert auth_service.check_login_rate_limit("ip-a", 500.0) is True
