"""Warming endpoint tests — thin routes over a mocked services.warming."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from schemas.warming import (
    WarmedAccount,
    WarmedAccountList,
    WarmingAccountState,
    WarmingBoardState,
    WarmingChannelList,
    WarmingSettings,
)
from services import warming as warming_service

if TYPE_CHECKING:
    from fastapi import FastAPI


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _settings() -> WarmingSettings:
    return WarmingSettings(gemini_model="gemini-2.5-flash", updated_at="now")


def _account() -> WarmingAccountState:
    return WarmingAccountState(account_id="acc-1", label="Main", state="active", health="ok")


@pytest.mark.asyncio
async def test_board_returns_state(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake() -> WarmingBoardState:
        return WarmingBoardState(
            idle=[],
            warming=[_account()],
            channels=WarmingChannelList(),
            settings=_settings(),
            channel_count=0,
            active_count=1,
        )

    monkeypatch.setattr("services.warming.load_board", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/warming/board")
    assert resp.status_code == 200
    assert resp.json()["active_count"] == 1


@pytest.mark.asyncio
async def test_start_returns_account_state(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(body: object) -> WarmingAccountState:  # noqa: ARG001
        return _account()

    monkeypatch.setattr("services.warming.start_warming", _fake)
    async with _client(app) as client:
        resp = await client.post("/api/v1/warming/start", json={"account_id": "acc-1"})
    assert resp.status_code == 200
    assert resp.json()["account_id"] == "acc-1"


@pytest.mark.asyncio
async def test_start_unknown_account_is_404(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(body: object) -> WarmingAccountState:  # noqa: ARG001
        raise warming_service.UnknownAccountError

    monkeypatch.setattr("services.warming.start_warming", _boom)
    async with _client(app) as client:
        resp = await client.post("/api/v1/warming/start", json={"account_id": "acc-x"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_start_not_ready_is_400(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(body: object) -> WarmingAccountState:  # noqa: ARG001
        raise warming_service.WarmingNotReadyError(["no proxy"])

    monkeypatch.setattr("services.warming.start_warming", _boom)
    async with _client(app) as client:
        resp = await client.post("/api/v1/warming/start", json={"account_id": "acc-1"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_add_channels(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(body: object) -> WarmingChannelList:  # noqa: ARG001
        return WarmingChannelList()

    monkeypatch.setattr("services.warming.add_channels", _fake)
    async with _client(app) as client:
        resp = await client.post("/api/v1/warming/channels", json={"raw": "@a, @b"})
    assert resp.status_code == 200
    assert resp.json() == {"channels": []}


@pytest.mark.asyncio
async def test_update_settings(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(body: object) -> WarmingSettings:  # noqa: ARG001
        return _settings()

    monkeypatch.setattr("services.warming.save_settings", _fake)
    async with _client(app) as client:
        resp = await client.put("/api/v1/warming/settings", json={"reactions_enabled": False})
    assert resp.status_code == 200
    assert resp.json()["gemini_model"] == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_warmed_lists_graduated_accounts(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake(min_days: int) -> WarmedAccountList:
        return WarmedAccountList(
            accounts=[
                WarmedAccount(
                    account_id="grad",
                    label="Graduate",
                    warming_days=20,
                    phone="+79261112233",
                    phone_country="RU",
                    proxy_type="socks5",
                    trust_score=88,
                    target_days=min_days,
                ),
            ],
        )

    monkeypatch.setattr("services.warming.list_warmed_accounts", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/warming/warmed")
    assert resp.status_code == 200
    account = resp.json()["accounts"][0]
    assert account["proxy_type"] == "socks5"
    assert account["trust_score"] == 88
    assert account["target_days"] == 14  # settings.neurocomment.warmed_min_days default


@pytest.mark.asyncio
async def test_promote_graduates_account(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(account_id: str) -> WarmingAccountState:
        return WarmingAccountState(
            account_id=account_id, label="X", state="idle", health="idle", promoted_to_nc=True
        )

    monkeypatch.setattr("services.warming.promote_to_neurocomment", _fake)
    async with _client(app) as client:
        resp = await client.post("/api/v1/warming/promote", json={"account_id": "acc-1"})
    assert resp.status_code == 200
    assert resp.json()["promoted_to_nc"] is True


@pytest.mark.asyncio
async def test_unpromote_clears_flag(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(account_id: str) -> WarmingAccountState:
        return WarmingAccountState(
            account_id=account_id, label="X", state="idle", health="idle", promoted_to_nc=False
        )

    monkeypatch.setattr("services.warming.unmark_neurocomment", _fake)
    async with _client(app) as client:
        resp = await client.post("/api/v1/warming/unpromote", json={"account_id": "acc-1"})
    assert resp.status_code == 200
    assert resp.json()["promoted_to_nc"] is False
