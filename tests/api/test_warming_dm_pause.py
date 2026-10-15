"""The fleet DM pause on ``/warming/settings``, the board, and ``/warming/dm-pause/lift``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import pytest

from core.repositories.logs import list_recent_logs
from core.repositories.warming_dm_pause import store_dm_paused_until

if TYPE_CHECKING:
    from fastapi import FastAPI


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_the_pause_shows_on_settings_and_board_and_the_lift_clears_it(app: FastAPI) -> None:
    until = (datetime.now(UTC) + timedelta(hours=72)).isoformat()
    await store_dm_paused_until(until)

    async with _client(app) as client:
        settings_resp = await client.get("/api/v1/warming/settings")
        board_resp = await client.get("/api/v1/warming/board")
        lift_resp = await client.post("/api/v1/warming/dm-pause/lift")
        after = await client.get("/api/v1/warming/settings")

    assert settings_resp.json()["dm_paused_until"] == until
    assert board_resp.json()["settings"]["dm_paused_until"] == until
    assert lift_resp.status_code == 200
    assert lift_resp.json()["dm_paused_until"] is None
    assert after.json()["dm_paused_until"] is None
    lifted = [e for e in await list_recent_logs() if e.event == "warming_dm_fleet_pause_lifted"]
    # Who reopened DMs for the whole fleet: the signed-in operator's login, nothing else.
    assert [entry.extra for entry in lifted] == [{"operator": "admin"}]


@pytest.mark.asyncio
async def test_a_lift_without_a_pause_logs_nothing(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/v1/warming/dm-pause/lift")

    assert resp.status_code == 200
    assert not [e for e in await list_recent_logs() if e.event == "warming_dm_fleet_pause_lifted"]


@pytest.mark.asyncio
async def test_an_expired_pause_reads_as_none(app: FastAPI) -> None:
    await store_dm_paused_until((datetime.now(UTC) - timedelta(minutes=1)).isoformat())

    async with _client(app) as client:
        resp = await client.get("/api/v1/warming/settings")

    assert resp.json()["dm_paused_until"] is None
