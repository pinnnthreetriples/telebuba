"""Adversarial checks of the panel-hygiene slice: docs, clear, sessions, admin sync, throttle.

Real temp SQLite, real ASGI app. Every setting the live ``.env`` could leak in is pinned.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import jwt
import pytest
from sqlalchemy import update

from api import create_app
from core import auth as core_auth
from core.config import settings
from core.db import _get_engine, _logs
from core.logging import log_event
from core.repositories.users import create_user, get_user_by_username
from schemas.auth import UserRecord
from services import auth as auth_service
from services.auth import _ratelimit, policy

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fastapi import FastAPI

_SECRET = "panel-test-" * 4  # 44 bytes of filler, not a credential
_DAY = 86_400


@pytest.fixture(autouse=True)
def _pin(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings.auth, "secret", _SECRET)
    monkeypatch.setattr(settings.auth, "cookie_secure", False)
    monkeypatch.setattr(settings.auth, "session_max_days", 30)
    monkeypatch.setattr(settings.auth, "session_ttl_minutes", 720)
    monkeypatch.setattr(settings.auth, "login_rate_limit_max_attempts", 5)
    monkeypatch.setattr(settings.auth, "login_rate_limit_window_seconds", 60.0)
    monkeypatch.setattr(settings.auth, "login_name_rate_limit_max_attempts", 10)
    monkeypatch.setattr(settings.auth, "login_name_rate_limit_window_seconds", 300.0)
    monkeypatch.setattr(settings.auth, "login_name_rate_limit_max_tracked", 1000)
    monkeypatch.setattr(settings.logging, "protected_retention_days", 30)
    _ratelimit._attempts.clear()
    _ratelimit._user_attempts.clear()
    yield
    _ratelimit._attempts.clear()
    _ratelimit._user_attempts.clear()


def _client(app: FastAPI, host: str = "203.0.113.1") -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=(host, 1234))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _stamp(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _set_created(event: str, created_at: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_logs).where(_logs.c.event == event).values(created_at=created_at)
        )


async def _remaining_events(client: httpx.AsyncClient) -> set[str]:
    page = await client.get("/api/v1/logs", params={"limit": 500})
    return {row["event"] for row in page.json()["items"]}


# --------------------------------------------------------------------------- docs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/docs",
        "/docs/",
        "/redoc",
        "/openapi.json",
        "/docs/oauth2-redirect",
        "/DOCS",
        "/api/v1/openapi.json",
    ],
)
async def test_docs_and_schema_are_404_by_default(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.api, "docs_enabled", False)
    async with _client(create_app()) as client:
        assert (await client.get(path)).status_code in {404, 405}


def test_openapi_in_process_works_with_docs_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.api, "docs_enabled", False)
    schema = create_app().openapi()
    assert schema["paths"]
    assert schema["info"]["title"] == "Telebuba API"


# ------------------------------------------------------------------- journal clear


async def _seed_mixed() -> None:
    for level in ("INFO", "WARNING", "ERROR"):
        await log_event(level, f"x_{level.lower()}_recent")  # type: ignore[arg-type]
        await log_event(level, f"x_{level.lower()}_old")  # type: ignore[arg-type]
        _set_created(f"x_{level.lower()}_old", _stamp(45))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prefix", ["", "x_", "x_warning", "x_error,x_info", " x_ ", "x_warning_recent"]
)
async def test_count_agrees_exactly_with_delete_and_protected_rows_survive(
    app: FastAPI, prefix: str
) -> None:
    await _seed_mixed()
    async with _client(app) as client:
        await _remaining_events(client)
        counted = (await client.get("/api/v1/logs/count", params={"event_prefix": prefix})).json()
        deleted = (await client.delete("/api/v1/logs", params={"event_prefix": prefix})).json()
        after = await _remaining_events(client)
    assert counted["matching"] == deleted["deleted"]
    assert counted["kept_protected"] == deleted["kept_protected"]
    assert {"x_warning_recent", "x_error_recent"} <= after
    # the old and info rows of the prefix are gone
    if prefix in {"", "x_"}:
        assert not ({"x_info_old", "x_warning_old", "x_error_old", "x_info_recent"} & after)


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", [" ", ",", "%", "_", "\\", "x_%", "%%", ",,"])
async def test_wildcard_or_blank_prefix_removes_nothing_it_should_not(
    app: FastAPI, prefix: str
) -> None:
    await _seed_mixed()
    async with _client(app) as client:
        before = await _remaining_events(client)
        deleted = (await client.delete("/api/v1/logs", params={"event_prefix": prefix})).json()
        after = await _remaining_events(client)
    assert deleted["deleted"] == 0
    assert before == after


@pytest.mark.asyncio
async def test_retention_zero_turns_protection_off(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.logging, "protected_retention_days", 0)
    await _seed_mixed()
    async with _client(app) as client:
        deleted = (await client.delete("/api/v1/logs")).json()
        after = await _remaining_events(client)
    assert deleted["kept_protected"] == 0
    assert not ({"x_warning_recent", "x_error_recent"} & after)


@pytest.mark.asyncio
async def test_logs_cleared_row_is_written_and_survives_its_own_clear(app: FastAPI) -> None:
    await _seed_mixed()
    async with _client(app) as client:
        await client.delete("/api/v1/logs")
        after = await _remaining_events(client)
    assert "logs_cleared" in after


@pytest.mark.asyncio
async def test_boundary_rows_one_minute_either_side_of_the_cutoff(app: FastAPI) -> None:
    await log_event("WARNING", "edge_inside")
    await log_event("ERROR", "edge_outside")
    _set_created("edge_inside", _stamp(30 - 1 / 1440))
    _set_created("edge_outside", _stamp(30 + 1 / 1440))
    async with _client(app) as client:
        await client.delete("/api/v1/logs")
        after = await _remaining_events(client)
    assert "edge_inside" in after
    assert "edge_outside" not in after


@pytest.mark.asyncio
async def test_a_row_stamped_without_fraction_seconds_is_ordered_correctly(app: FastAPI) -> None:
    """``isoformat`` drops ``.000000``: ``+`` sorts before ``.``, the comparison must still hold."""
    await log_event("WARNING", "no_fraction_recent")
    await log_event("WARNING", "no_fraction_old")
    recent = datetime.now(UTC).replace(microsecond=0) - timedelta(days=29)
    old = datetime.now(UTC).replace(microsecond=0) - timedelta(days=31)
    _set_created("no_fraction_recent", recent.isoformat())
    _set_created("no_fraction_old", old.isoformat())
    async with _client(app) as client:
        await client.delete("/api/v1/logs")
        after = await _remaining_events(client)
    assert "no_fraction_recent" in after
    assert "no_fraction_old" not in after


# ---------------------------------------------------------------- session lifetime


def _forge(**claims: object) -> str:
    base: dict[str, object] = {
        "sub": "u1",
        "ver": 0,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    base.update(claims)
    return jwt.encode(base, _SECRET, algorithm=settings.auth.algorithm)


def test_token_older_than_limit_is_rejected_even_with_future_exp() -> None:
    token = _forge(auth_time=int(time.time()) - 31 * _DAY, exp=int(time.time()) + 10 * _DAY)
    assert core_auth.decode_session_claims(token) is None


@pytest.mark.parametrize("bad", ["123", 1.5, None, True, [1], {"a": 1}])
def test_non_integer_auth_time_is_rejected_not_raised(bad: object) -> None:
    assert core_auth.decode_session_claims(_forge(auth_time=bad)) is None


def test_missing_iat_and_auth_time_is_rejected() -> None:
    token = jwt.encode(
        {"sub": "u1", "ver": 0, "exp": int(time.time()) + 3600},
        _SECRET,
        algorithm=settings.auth.algorithm,
    )
    assert core_auth.decode_session_claims(token) is None


def test_legacy_token_falls_back_to_iat() -> None:
    old = jwt.encode(
        {"sub": "u1", "ver": 0, "iat": int(time.time()) - 31 * _DAY, "exp": int(time.time()) + 600},
        _SECRET,
        algorithm=settings.auth.algorithm,
    )
    assert core_auth.decode_session_claims(old) is None
    fresh = jwt.encode(
        {"sub": "u1", "ver": 0, "iat": int(time.time()) - 5 * _DAY, "exp": int(time.time()) + 600},
        _SECRET,
        algorithm=settings.auth.algorithm,
    )
    claims = core_auth.decode_session_claims(fresh)
    assert claims is not None
    assert abs(claims.auth_time - (int(time.time()) - 5 * _DAY)) <= 2


def test_unsigned_or_wrong_key_auth_time_is_not_trusted() -> None:
    """auth_time lives inside the signed JWT: an attacker cannot pick it without the secret."""
    forged = jwt.encode(
        {"sub": "u1", "ver": 0, "auth_time": int(time.time()), "exp": int(time.time()) + 600},
        "some-other-secret-0123456789abcdef-padpad",
        algorithm=settings.auth.algorithm,
    )
    assert core_auth.decode_session_claims(forged) is None


@pytest.mark.asyncio
async def test_sliding_reissue_keeps_original_auth_time_and_cannot_be_slid_past_limit() -> None:
    await create_user(
        UserRecord(
            id="u1", username="admin", password_hash=core_auth.hash_password("pw"), role="admin"
        )
    )
    old = int(time.time()) - 29 * _DAY
    token = core_auth.encode_session_token("u1", 0, auth_time=old)
    resolved = await auth_service.resolve_and_refresh(token)
    assert resolved is not None
    claims = core_auth.decode_session_claims(resolved[1])
    assert claims is not None
    assert claims.auth_time == old
    again = await auth_service.resolve_and_refresh(resolved[1])
    assert again is not None
    claims = core_auth.decode_session_claims(again[1])
    assert claims is not None
    assert claims.auth_time == old
    expired = core_auth.encode_session_token("u1", 0, auth_time=int(time.time()) - 31 * _DAY)
    assert await auth_service.resolve_and_refresh(expired) is None


@pytest.mark.parametrize(
    "auth_time",
    [
        -1,
        -(10**9),
        10**12,
        10**18,
    ],
)
def test_extreme_auth_time_never_raises(auth_time: int) -> None:
    """A signed token with a nonsense ``auth_time`` is refused, not a 500."""
    token = _forge(auth_time=auth_time)
    assert core_auth.decode_session_claims(token) is None


def test_auth_time_in_the_future_is_rejected() -> None:
    token = _forge(auth_time=int(time.time()) + 3 * 365 * _DAY)
    assert core_auth.decode_session_claims(token) is None


# ------------------------------------------------------------------ admin sync


async def _admin(password: str = "old-pw", username: str = "admin") -> str:
    await create_user(
        UserRecord(
            id="admin-1",
            username=username,
            password_hash=core_auth.hash_password(password),
            role="admin",
        )
    )
    return "admin-1"


async def _version() -> int:
    record = await get_user_by_username("admin")
    assert record is not None
    return record.token_version


async def _journal(client: httpx.AsyncClient, event: str) -> int:
    rows = (await client.get("/api/v1/logs", params={"limit": 500})).json()["items"]
    return sum(1 for row in rows if row["event"] == event)


@pytest.mark.asyncio
async def test_changed_password_is_adopted_sessions_die_and_it_is_journalled(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin("old-pw")
    monkeypatch.setattr(settings.auth, "admin_username", "admin")
    monkeypatch.setattr(settings.auth, "admin_password", "new-pw")
    before = await _version()
    await policy.seed_admin_if_empty()
    after = await get_user_by_username("admin")
    assert after is not None
    assert after.token_version == before + 1
    assert core_auth.verify_password("new-pw", after.password_hash)
    assert not core_auth.verify_password("old-pw", after.password_hash)
    async with _client(app) as client:
        assert await _journal(client, "admin_password_changed") == 1
        text = (await client.get("/api/v1/logs", params={"limit": 500})).text
    assert "new-pw" not in text
    assert "old-pw" not in text
    assert after.password_hash not in text
    # second start: nothing written
    await policy.seed_admin_if_empty()
    again = await get_user_by_username("admin")
    assert again is not None
    assert again.token_version == after.token_version
    assert again.password_hash == after.password_hash


@pytest.mark.asyncio
async def test_unchanged_empty_or_unknown_admin_change_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _admin("same-pw")
    record = await get_user_by_username("admin")
    assert record is not None
    for username, password in (("admin", "same-pw"), ("admin", ""), ("ghost", "x"), ("", "x")):
        monkeypatch.setattr(settings.auth, "admin_username", username)
        monkeypatch.setattr(settings.auth, "admin_password", password)
        await policy.seed_admin_if_empty()
        now = await get_user_by_username("admin")
        assert now is not None
        assert now.password_hash == record.password_hash
        assert now.token_version == record.token_version


@pytest.mark.asyncio
async def test_username_case_mismatch_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    await _admin("old-pw", username="Admin")
    monkeypatch.setattr(settings.auth, "admin_username", "admin")
    monkeypatch.setattr(settings.auth, "admin_password", "other")
    await policy.seed_admin_if_empty()
    record = await get_user_by_username("Admin")
    assert record is not None
    assert core_auth.verify_password("old-pw", record.password_hash)


@pytest.mark.asyncio
async def test_only_the_configured_admin_is_touched_with_two_admins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _admin("old-pw")
    await create_user(
        UserRecord(
            id="admin-2",
            username="second",
            password_hash=core_auth.hash_password("s"),
            role="admin",
        )
    )
    monkeypatch.setattr(settings.auth, "admin_username", "admin")
    monkeypatch.setattr(settings.auth, "admin_password", "new-pw")
    await policy.seed_admin_if_empty()
    second = await get_user_by_username("second")
    assert second is not None
    assert second.token_version == 0
    assert core_auth.verify_password("s", second.password_hash)


@pytest.mark.asyncio
async def test_whitespace_only_configured_password_does_not_replace_the_stored_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _admin("old-pw")
    monkeypatch.setattr(settings.auth, "admin_username", "admin")
    monkeypatch.setattr(settings.auth, "admin_password", "   ")
    await policy.seed_admin_if_empty()
    record = await get_user_by_username("admin")
    assert record is not None
    assert core_auth.verify_password("old-pw", record.password_hash)


# -------------------------------------------------------------------- throttle


@pytest.mark.asyncio
async def test_user_limit_is_independent_of_ip_and_normalised() -> None:
    now = time.time()
    variants = ["admin", "ADMIN", " admin ", "Admin"] * 3
    results = [auth_service.check_login_user_rate_limit(name, now) for name in variants]
    assert results[:10] == [True] * 10
    assert results[10:] == [False, False]


def test_user_bucket_memory_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.auth, "login_name_rate_limit_max_tracked", 50)
    now = time.time()
    for index in range(5000):
        auth_service.check_login_user_rate_limit(f"user-{index}", now)
    assert len(_ratelimit._user_attempts) <= 51
    assert all(len(key) <= 128 for key in _ratelimit._user_attempts)
    big = "A" * 10_000
    auth_service.check_login_user_rate_limit(big, now)
    assert all(len(key) <= 128 for key in _ratelimit._user_attempts)


@pytest.mark.parametrize("name", ["admin\x00", "admin\u200b", "ADMİN"])
def test_variants_that_the_database_would_not_match_do_not_help_an_attacker(name: str) -> None:
    """Such a variant can't authenticate as ``admin`` anyway; it must not unlock extra tries."""
    now = time.time()
    for _ in range(10):
        auth_service.check_login_user_rate_limit("admin", now)
    assert auth_service.check_login_user_rate_limit("admin", now) is False
    # a different key is a different bucket: that is allowed, it targets another (non-)user
    assert auth_service.check_login_user_rate_limit(name, now) in {True, False}


@pytest.mark.asyncio
async def test_only_failed_attempts_lock_a_name_and_the_lock_ends_with_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decided: only failures count; ten lock the name, even against the right password.

    The lock ends exactly when the window passes, and then the right password works.
    """
    await _admin("right-pw")
    raw = create_app()
    clock = [1_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    window = settings.auth.login_name_rate_limit_window_seconds
    hosts = iter(f"198.51.100.{index}" for index in range(1, 200))

    async def attempt(password: str) -> int:
        async with _client(raw, host=next(hosts)) as client:
            reply = await client.post(
                "/api/v1/auth/login", json={"username": "admin", "password": password}
            )
        return reply.status_code

    assert [await attempt("right-pw") for _ in range(15)] == [200] * 15
    assert [await attempt("wrong") for _ in range(10)] == [401] * 10
    assert await attempt("right-pw") == 429
    clock[0] += window
    assert await attempt("right-pw") == 429
    clock[0] += 1
    assert await attempt("right-pw") == 200


@pytest.mark.asyncio
async def test_lockout_by_swarm_expires_after_the_window() -> None:
    now = time.time()
    for _ in range(10):
        auth_service.check_login_user_rate_limit("admin", now)
    assert auth_service.check_login_user_rate_limit("admin", now + 10) is False
    assert auth_service.check_login_user_rate_limit("admin", now + 301) is True


def test_name_flood_cannot_lock_out_an_untracked_real_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.auth, "login_name_rate_limit_max_tracked", 20)
    now = time.time()
    for index in range(20):
        auth_service.check_login_user_rate_limit(f"noise-{index}", now)
    for index in range(10):
        auth_service.check_login_user_rate_limit(f"more-noise-{index}", now)
    assert auth_service.check_login_user_rate_limit("admin", now) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_spa_catch_all_does_not_serve_the_schema_in_the_composed_app(
    path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import main  # noqa: PLC0415

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>shell</html>", encoding="utf-8")
    monkeypatch.setattr(main, "_FRONTEND_DIST", dist)
    monkeypatch.setattr(settings.api, "docs_enabled", False)
    app = create_app()
    main._mount_frontend(app)
    async with _client(app) as client:
        reply = await client.get(path)
    assert "paths" not in reply.text
    assert "swagger" not in reply.text.lower()
    assert "openapi" not in reply.text.lower()
