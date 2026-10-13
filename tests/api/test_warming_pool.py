"""«Найти каналы» routes (item 4.5): thin routes over ``services.warming.pool_fill``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories.warming_pool import propose_warming_channels
from schemas._warming_pool import PoolFillRequest, PoolFillResult
from schemas.warming import WarmingChannel
from services.warming import pool_fill
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.mark.asyncio
async def test_find_forwards_the_request(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[PoolFillRequest] = []

    async def find(request: PoolFillRequest) -> PoolFillResult:
        seen.append(request)
        return PoolFillResult(status="ok", found=3, checked=2, proposed=1)

    monkeypatch.setattr(pool_fill, "find_channels", find)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/warming/channels/find",
            json={"language": "es", "country": "MX", "topics": ["fútbol", "cine"]},
        )
    assert resp.status_code == 200
    assert resp.json()["proposed"] == 1
    assert seen[0].topics == ["fútbol", "cine"]


@pytest.mark.asyncio
async def test_a_request_without_topics_is_refused(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/warming/channels/find", json={"language": "es", "topics": []}
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_proposals_are_listed_approved_and_rejected(app: FastAPI) -> None:
    await propose_warming_channels(
        [
            WarmingChannel(channel=name, created_at="2026-01-01T00:00:00+00:00", status="pending")
            for name in ("a_mx", "b_mx")
        ]
    )
    async with _client(app) as client:
        listed = await client.get("/api/v1/warming/channels/proposals")
        approved = await client.post(
            "/api/v1/warming/channels/proposals/approve", json={"channels": ["a_mx"]}
        )
        rejected = await client.post(
            "/api/v1/warming/channels/proposals/reject", json={"channels": ["b_mx"]}
        )
        pool = await client.get("/api/v1/warming/channels")
    assert [c["channel"] for c in listed.json()["channels"]] == ["a_mx", "b_mx"]
    assert [c["channel"] for c in approved.json()["channels"]] == ["b_mx"]
    assert rejected.json()["channels"] == []
    assert [c["channel"] for c in pool.json()["channels"]] == ["a_mx"]
