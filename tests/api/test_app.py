"""API foundation tests — health, the accounts seed endpoint, and the error envelope."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from api import create_app
from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate
from services.accounts import add_account

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fastapi import FastAPI


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


@pytest.fixture
def app() -> FastAPI:
    # No lifespan in tests: the warming/neurocomment runtimes must not start.
    return create_app()


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_health_returns_ok(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_accounts_returns_page_envelope(app: FastAPI) -> None:
    await add_account(AccountCreate(account_id="acc-1", label="One"))
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "next_cursor"}
    assert [a["account_id"] for a in body["items"]] == ["acc-1"]
    assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_accounts_pagination_emits_next_cursor(app: FastAPI) -> None:
    for i in range(3):
        await add_account(AccountCreate(account_id=f"acc-{i}", label=f"A{i}"))
    async with _client(app) as client:
        first = await client.get("/api/v1/accounts", params={"limit": 2})
        assert first.status_code == 200
        page1 = first.json()
        assert len(page1["items"]) == 2
        assert page1["next_cursor"] == "2"

        second = await client.get(
            "/api/v1/accounts",
            params={"limit": 2, "cursor": page1["next_cursor"]},
        )
    page2 = second.json()
    assert len(page2["items"]) == 1
    assert page2["next_cursor"] is None


@pytest.mark.asyncio
async def test_invalid_cursor_returns_error_envelope(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts", params={"cursor": "not-an-int"})
    assert resp.status_code == 400
    assert resp.json() == {"error": {"code": "bad_request", "message": "invalid pagination cursor"}}


@pytest.mark.asyncio
async def test_validation_error_is_remapped_into_envelope(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts", params={"limit": 0})  # below ge=1
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "validation_error"
    # The offending field path is reported so the SPA can attach it to the input.
    assert any("limit" in key for key in error["fields"])


@pytest.mark.asyncio
async def test_unexpected_error_returns_generic_envelope(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(**_kwargs: object) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr("services.accounts.list_accounts_page", _boom)
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts")
    assert resp.status_code == 500
    assert resp.json() == {"error": {"code": "internal_error", "message": "Internal server error"}}
