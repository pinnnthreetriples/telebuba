"""Auth endpoint tests — real login/logout/me flow + the protected-route gate.

These build a raw ``create_app()`` (no get_current_user override) so the auth
gate is genuinely exercised. cookie_secure is off so httpx resends the cookie
over http; the limiter is reset per test (it is process-global, in-memory).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from api import create_app
from core import auth as core_auth
from core.config import settings
from core.repositories.users import create_user
from schemas.auth import UserRecord
from services.auth import _ratelimit

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings.auth, "secret", "api-test-secret-0123456789abcdef-pad")
    monkeypatch.setattr(settings.auth, "cookie_secure", False)
    _ratelimit._attempts.clear()  # reset the process-global limiter
    yield
    _ratelimit._attempts.clear()


def _raw_app() -> FastAPI:
    return create_app()


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _seed_admin(password: str = "pw") -> None:
    await create_user(
        UserRecord(
            id="admin-1",
            username="admin",
            password_hash=core_auth.hash_password(password),
            role="admin",
        ),
    )


@pytest.mark.asyncio
async def test_login_sets_cookie_and_me_returns_the_user() -> None:
    await _seed_admin()
    async with _client(_raw_app()) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "pw"},
        )
        assert login.status_code == 200
        assert login.json()["username"] == "admin"
        assert settings.auth.cookie_name in login.cookies
        me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "admin"


@pytest.mark.asyncio
async def test_login_rejects_wrong_credentials() -> None:
    await _seed_admin()
    async with _client(_raw_app()) as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "nope"},
        )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_login_refuses_when_secret_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.auth, "secret", "")
    async with _client(_raw_app()) as client:
        resp = await client.post("/api/v1/auth/login", json={"username": "a", "password": "b"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "unavailable"


@pytest.mark.asyncio
async def test_login_is_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.auth, "login_rate_limit_max_attempts", 1)
    await _seed_admin()
    creds = {"username": "admin", "password": "x"}
    async with _client(_raw_app()) as client:
        first = await client.post("/api/v1/auth/login", json=creds)
        second = await client.post("/api/v1/auth/login", json=creds)
    assert first.status_code == 401
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"


@pytest.mark.asyncio
async def test_me_without_cookie_is_unauthorized() -> None:
    async with _client(_raw_app()) as client:
        resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logout_clears_the_session() -> None:
    await _seed_admin()
    async with _client(_raw_app()) as client:
        await client.post("/api/v1/auth/login", json={"username": "admin", "password": "pw"})
        logout = await client.post("/api/v1/auth/logout")
        assert logout.status_code == 204
        me = await client.get("/api/v1/auth/me")
    assert me.status_code == 401


@pytest.mark.asyncio
async def test_protected_endpoint_rejects_anonymous_requests() -> None:
    async with _client(_raw_app()) as client:
        resp = await client.get("/api/v1/accounts")
    assert resp.status_code == 401
