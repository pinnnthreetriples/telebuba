"""``/warming/rewarm`` («Вернуть в прогрев») and the geo consent on ``/warming/start``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from core.db import (
    create_account,
    fetch_warming_state,
    mark_promoted_to_nc,
    save_warming_settings,
    upsert_warming_state,
)
from core.repositories.lifecycle import list_lifecycle_events
from schemas.accounts import AccountCreate
from schemas.warming import (
    StartWarmingRequest,
    WarmingAccountState,
    WarmingRefusalCode,
    WarmingStateWrite,
)
from services import _account_owner
from services import warming as warming_service
from services.warming import _runtime
from tests.factories import seed_account_proxy
from tests.services.warming._support import _fake_loop

if TYPE_CHECKING:
    from fastapi import FastAPI


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _card(account_id: str) -> WarmingAccountState:
    return WarmingAccountState(account_id=account_id, label="Main", state="active", health="ok")


@pytest.mark.asyncio
async def test_rewarm_forwards_the_dialog_choice(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[StartWarmingRequest] = []

    async def _fake(body: StartWarmingRequest) -> WarmingAccountState:
        seen.append(body)
        return _card(body.account_id)

    monkeypatch.setattr("services.warming.rewarm_account", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/warming/rewarm",
            json={"account_id": "acc-1", "target_days": 21, "accept_geo_mismatch": True},
        )
    assert resp.status_code == 200
    assert (seen[0].target_days, seen[0].accept_geo_mismatch) == (21, True)


@pytest.mark.asyncio
async def test_start_carries_the_geo_consent(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
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
        (warming_service.NotGraduatedError("acc-1"), 409),
    ],
)
@pytest.mark.asyncio
async def test_rewarm_maps_refusals_like_start(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status: int,
) -> None:
    async def _boom(body: StartWarmingRequest) -> WarmingAccountState:
        del body
        raise error

    monkeypatch.setattr("services.warming.rewarm_account", _boom)
    async with _client(app) as client:
        resp = await client.post("/api/v1/warming/rewarm", json={"account_id": "acc-1"})
    assert resp.status_code == status


@pytest.mark.asyncio
async def test_rewarm_names_a_stale_warmed_card_by_its_code(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(body: StartWarmingRequest) -> WarmingAccountState:
        raise warming_service.NotGraduatedError(body.account_id)

    monkeypatch.setattr("services.warming.rewarm_account", _boom)
    async with _client(app) as client:
        resp = await client.post("/api/v1/warming/rewarm", json={"account_id": "acc-1"})
    assert resp.status_code == 409
    assert resp.json()["error"]["message"] == "not_graduated"


@pytest.mark.asyncio
async def test_the_old_endpoints_are_gone(app: FastAPI) -> None:
    async with _client(app) as client:
        resume = await client.post("/api/v1/warming/resume", json={"account_id": "acc-1"})
        unpromote = await client.post("/api/v1/warming/unpromote", json={"account_id": "acc-1"})
    assert (resume.status_code, unpromote.status_code) == (404, 404)


@pytest.mark.asyncio
async def test_rewarm_reports_a_busy_account_by_its_code(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(body: StartWarmingRequest) -> WarmingAccountState:
        code: WarmingRefusalCode = "account_cooling"
        raise warming_service.AccountUnavailableError(code, body.account_id)

    monkeypatch.setattr("services.warming.rewarm_account", _boom)
    async with _client(app) as client:
        resp = await client.post("/api/v1/warming/rewarm", json={"account_id": "acc-1"})
    assert resp.status_code == 409
    assert resp.json()["error"]["message"] == "account_cooling"


@pytest.mark.asyncio
async def test_rewarm_end_to_end_starts_a_fresh_second_wave(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The round button's endpoint, unmocked down to the loop coroutine itself."""
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    _account_owner.reset_for_tests()
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1", phone="+12025550123"))
    await seed_account_proxy("acc-1", host="1.2.3.4")
    old_start = "2026-09-01T00:00:00+00:00"
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="idle",
            started_at=old_start,
            target_days=14,
            warmed_days=14,
            last_warmed_date="2026-09-15",
        ),
    )
    await mark_promoted_to_nc("acc-1")
    try:
        async with _client(app) as client:
            resp = await client.post(
                "/api/v1/warming/rewarm",
                json={"account_id": "acc-1", "target_days": 10, "activity_persona": "calm"},
            )
            board = (await client.get("/api/v1/warming/board")).json()
    finally:
        for task in list(warming_service._RUNTIME.values()):
            task.cancel()
        _account_owner.reset_for_tests()

    assert resp.status_code == 200
    body = resp.json()
    assert (body["state"], body["wave"], body["warmed_days"]) == ("active", 2, 0)
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.started_at != old_start
    assert (record.target_days, record.promoted_to_nc) == (10, False)
    # Off the warmed card, onto the board as wave 2.
    assert [card["account_id"] for card in board["warming"]] == ["acc-1"]
    assert board["warmed"] == []
    started = [e for e in await list_lifecycle_events("acc-1") if e.kind == "warming_started"]
    assert started[-1].extra["wave"] == 2
    assert started[-1].extra["new_stint"] is True
