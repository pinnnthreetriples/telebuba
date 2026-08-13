"""services/auth — authenticate, resolve, seed, and the login rate limiter."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING

import pytest

from core import auth as core_auth
from core.config import settings
from core.db import configure_database
from core.repositories.users import count_users, create_user, get_user_by_username
from schemas.auth import LoginRequest, UserRecord
from services import auth as auth_service
from services.auth import _argon2

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
async def test_authenticate_keeps_argon2_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_user()
    release = threading.Event()

    def _blocking_verify(_password: str, _password_hash: str) -> bool:
        return release.wait(timeout=1.0)

    monkeypatch.setattr(_argon2.core_auth, "verify_password", _blocking_verify)
    asyncio.get_running_loop().call_later(0.02, release.set)
    started = time.monotonic()
    user = await auth_service.authenticate(LoginRequest(username="admin", password="pw"))

    assert user is not None
    # If Argon2 ran on the loop, call_later could not release it until the 1 s timeout.
    assert time.monotonic() - started < 0.5


@pytest.mark.asyncio
async def test_authenticate_bounds_parallel_argon2_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_user()
    active = 0
    peak = 0
    lock = threading.Lock()
    release = threading.Event()

    def _slow_verify(_password: str, _password_hash: str) -> bool:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        release.wait(timeout=1.0)
        with lock:
            active -= 1
        return True

    monkeypatch.setattr(_argon2.core_auth, "verify_password", _slow_verify)
    credentials = LoginRequest(username="admin", password="pw")
    tasks = [asyncio.create_task(auth_service.authenticate(credentials)) for _ in range(6)]
    for _ in range(100):
        with lock:
            active_full = active == settings.auth.argon2_max_concurrency
        refused_count = sum(task.done() for task in tasks)
        if active_full and refused_count >= len(tasks) - settings.auth.argon2_max_concurrency:
            break
        await asyncio.sleep(0.005)
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    admitted = [result for result in results if not isinstance(result, Exception)]
    refused = [result for result in results if isinstance(result, Exception)]
    assert len(admitted) == settings.auth.argon2_max_concurrency
    assert all(user is not None for user in admitted)
    assert all(isinstance(error, auth_service.AuthenticationCapacityError) for error in refused)
    assert peak == min(settings.auth.argon2_max_concurrency, len(results))


@pytest.mark.asyncio
async def test_cancelling_login_does_not_release_running_argon2_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_user()
    started = threading.Event()
    release = threading.Event()

    def _blocking_verify(_password: str, _password_hash: str) -> bool:
        started.set()
        return release.wait(timeout=1.0)

    monkeypatch.setattr(settings.auth, "argon2_max_concurrency", 1)
    monkeypatch.setattr(_argon2.core_auth, "verify_password", _blocking_verify)
    credentials = LoginRequest(username="admin", password="pw")
    first = asyncio.create_task(auth_service.authenticate(credentials))
    await asyncio.to_thread(started.wait, 1.0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    with pytest.raises(auth_service.AuthenticationCapacityError):
        await auth_service.authenticate(credentials)

    release.set()
    for _ in range(100):
        try:
            user = await auth_service.authenticate(credentials)
        except auth_service.AuthenticationCapacityError:
            await asyncio.sleep(0.005)
            continue
        assert user is not None
        break
    else:  # pragma: no cover - worker completion is bounded by the test event
        pytest.fail("Argon2 slot was not released after the worker completed")


@pytest.mark.asyncio
async def test_resolve_user_round_trips_a_session_token() -> None:
    await _seed_user()
    token = await auth_service.issue_session_token("u1")
    resolved = await auth_service.resolve_user(token)
    assert resolved is not None
    assert resolved.id == "u1"


@pytest.mark.asyncio
async def test_resolve_user_rejects_a_bad_token() -> None:
    assert await auth_service.resolve_user("garbage") is None


@pytest.mark.asyncio
async def test_revoke_sessions_invalidates_outstanding_tokens() -> None:
    await _seed_user()
    token = await auth_service.issue_session_token("u1")
    assert await auth_service.resolve_user(token) is not None
    # Logout bumps the user's token_version; the old token's ``ver`` no longer
    # matches, so it must be rejected even though it has not expired.
    await auth_service.revoke_sessions("u1")
    assert await auth_service.resolve_user(token) is None
    # A freshly minted token (post-bump version) is accepted again.
    fresh = await auth_service.issue_session_token("u1")
    assert await auth_service.resolve_user(fresh) is not None


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


def test_rate_limiter_evicts_stale_buckets(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.auth import _ratelimit  # noqa: PLC0415 - reach the process-global dict.

    _ratelimit._attempts.clear()
    monkeypatch.setattr(settings.auth, "login_rate_limit_max_attempts", 5)
    monkeypatch.setattr(settings.auth, "login_rate_limit_window_seconds", 100.0)
    # A one-shot client (ip-a) never comes back; a later client (ip-b) hits the
    # limiter well after ip-a's window has elapsed. ip-a's fully-expired bucket
    # must be evicted, not retained forever (memory leak). Only ip-b remains.
    assert auth_service.check_login_rate_limit("ip-a", 1.0) is True
    assert auth_service.check_login_rate_limit("ip-b", 1_000.0) is True
    assert set(_ratelimit._attempts) == {"ip-b"}
    _ratelimit._attempts.clear()
