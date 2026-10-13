"""The start delay on ``/warming/start`` and the read-only ``/warming/start-preview``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from schemas._warming_start import StartPreview, StartPreviewItem, StartPreviewRequest
from schemas.warming import (
    StartWarmingRequest,
    WarmingAccountState,
)

if TYPE_CHECKING:
    from fastapi import FastAPI


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_start_forwards_the_delay_and_the_pool_spread(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[StartWarmingRequest] = []

    async def _fake(body: StartWarmingRequest) -> WarmingAccountState:
        seen.append(body)
        return WarmingAccountState(
            account_id=body.account_id, label="Main", state="active", health="ok"
        )

    monkeypatch.setattr("services.warming.start_warming_with_consent", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/warming/start",
            json={"account_id": "acc-1", "start_delay_hours": 6, "spread_start": True},
        )
    assert resp.status_code == 200
    assert (seen[0].start_delay_hours, seen[0].spread_start) == (6, True)


@pytest.mark.asyncio
@pytest.mark.parametrize("hours", [0, 25])
async def test_start_rejects_a_delay_outside_one_to_twenty_four_hours(
    app: FastAPI, hours: int
) -> None:
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/warming/start", json={"account_id": "acc-1", "start_delay_hours": hours}
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_preview_returns_each_account_window(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake(body: StartPreviewRequest) -> StartPreview:
        return StartPreview(
            items=[
                StartPreviewItem(
                    account_id=account_id,
                    time_zone="UTC",
                    earliest_at="2026-06-13T20:00:00+00:00",
                    latest_at="2026-06-14T08:00:00+00:00",
                    shifted=body.spread_start,
                )
                for account_id in body.account_ids
            ]
        )

    monkeypatch.setattr("services.warming.preview_start", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/warming/start-preview",
            json={"account_ids": ["a", "b"], "start_delay_hours": 12, "spread_start": True},
        )
    assert resp.status_code == 200
    assert [item["account_id"] for item in resp.json()["items"]] == ["a", "b"]
    assert resp.json()["items"][0]["shifted"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"account_ids": ["a"], "start_delay_hours": 0},
        {"account_ids": ["a"], "start_delay_hours": 25},
        {"account_ids": [], "start_delay_hours": 3},
        {"account_ids": ["a"]},
    ],
)
async def test_preview_validates_its_body(app: FastAPI, body: dict[str, object]) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/v1/warming/start-preview", json=body)
    assert resp.status_code == 422
