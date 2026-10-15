"""«Найти каналы» routes (item 4.5): thin routes over ``services.warming.pool_fill``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from schemas._warming_pool import PoolFillRequest, PoolFillResult
from services.warming import pool_fill
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.mark.asyncio
async def test_find_forwards_the_request(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[PoolFillRequest] = []

    async def find(request: PoolFillRequest) -> PoolFillResult:
        seen.append(request)
        return PoolFillResult(status="ok", found=3, checked=2, added=1)

    monkeypatch.setattr(pool_fill, "find_channels", find)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/warming/channels/find",
            json={"language": "es", "country": "MX", "topics": ["fútbol", "cine"]},
        )
    assert resp.status_code == 200
    assert resp.json()["added"] == 1
    assert seen[0].topics == ["fútbol", "cine"]


@pytest.mark.asyncio
async def test_a_request_with_too_many_topics_is_refused(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/warming/channels/find",
            json={"language": "es", "topics": ["aa", "bb", "cc", "dd", "ee", "ff"]},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_the_proposal_routes_are_gone(app: FastAPI) -> None:
    async with _client(app) as client:
        listed = await client.get("/api/v1/warming/channels/proposals")
        approved = await client.post(
            "/api/v1/warming/channels/proposals/approve", json={"channels": ["a_mx"]}
        )
    assert listed.status_code in {404, 405}
    assert approved.status_code in {404, 405}


@pytest.mark.asyncio
async def test_consent_is_forwarded(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[PoolFillRequest] = []

    async def find(request: PoolFillRequest) -> PoolFillResult:
        seen.append(request)
        return PoolFillResult(status="ok")

    monkeypatch.setattr(pool_fill, "find_channels", find)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/warming/channels/find",
            json={"language": "es", "pull_account_id": "acc-9"},
        )
    assert resp.status_code == 200
    assert (seen[0].topics, seen[0].pull_account_id) == ([], "acc-9")
