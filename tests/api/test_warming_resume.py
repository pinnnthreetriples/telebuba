"""``/warming/resume`` («Вернуть в прогрев») and the geo consent on ``/warming/start``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from schemas.warming import StartWarmingRequest, WarmingAccountState
from services import warming as warming_service

if TYPE_CHECKING:
    from fastapi import FastAPI


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _card(account_id: str) -> WarmingAccountState:
    return WarmingAccountState(account_id=account_id, label="Main", state="active", health="ok")


@pytest.mark.asyncio
async def test_resume_forwards_the_dialog_choice(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[StartWarmingRequest] = []

    async def _fake(body: StartWarmingRequest) -> WarmingAccountState:
        seen.append(body)
        return _card(body.account_id)

    monkeypatch.setattr("services.warming.resume_warming", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/warming/resume",
            json={"account_id": "acc-1", "target_days": 21, "accept_geo_mismatch": True},
        )
    assert resp.status_code == 200
    assert (seen[0].target_days, seen[0].accept_geo_mismatch) == (21, True)


@pytest.mark.asyncio
async def test_start_carries_the_geo_consent(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[StartWarmingRequest] = []

    async def _fake(body: StartWarmingRequest) -> WarmingAccountState:
        seen.append(body)
        return _card(body.account_id)

    monkeypatch.setattr("services.warming.start_warming_with_consent", _fake)
    async with _client(app) as client:
        plain = await client.post("/api/v1/warming/start", json={"account_id": "acc-1"})
        accepted = await client.post(
            "/api/v1/warming/start", json={"account_id": "acc-1", "accept_geo_mismatch": True}
        )
    assert (plain.status_code, accepted.status_code) == (200, 200)
    assert [body.accept_geo_mismatch for body in seen] == [False, True]


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (warming_service.UnknownAccountError("acc-1"), 404),
        (warming_service.WarmingTaskNotQuiescentError("acc-1"), 409),
        (warming_service.WarmingNotReadyError(["no proxy"]), 400),
        (warming_service.AccountIsListenerError("acc-1"), 409),
    ],
)
@pytest.mark.asyncio
async def test_resume_maps_refusals_like_start(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status: int,
) -> None:
    async def _boom(body: StartWarmingRequest) -> WarmingAccountState:
        del body
        raise error

    monkeypatch.setattr("services.warming.resume_warming", _boom)
    async with _client(app) as client:
        resp = await client.post("/api/v1/warming/resume", json={"account_id": "acc-1"})
    assert resp.status_code == status


@pytest.mark.asyncio
async def test_resume_reports_a_busy_account_by_its_code(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(body: StartWarmingRequest) -> WarmingAccountState:
        raise warming_service.AccountUnavailableError("account_cooling", body.account_id)

    monkeypatch.setattr("services.warming.resume_warming", _boom)
    async with _client(app) as client:
        resp = await client.post("/api/v1/warming/resume", json={"account_id": "acc-1"})
    assert resp.status_code == 409
    assert resp.json()["error"]["message"] == "account_cooling"
