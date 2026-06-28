"""Neurocomment endpoint tests — thin routes over a mocked services.neurocomment."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from schemas.neurocomment import (
    CampaignList,
    ChannelLinkOutcome,
    NeurocommentBoard,
    NeurocommentCampaign,
    NeurocommentRuntimeStatus,
)

if TYPE_CHECKING:
    from fastapi import FastAPI


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _campaign() -> NeurocommentCampaign:
    return NeurocommentCampaign(
        campaign_id="c1",
        name="Promo",
        prompt="mention the product",
        status="active",
        created_at="now",
        updated_at="now",
    )


@pytest.mark.asyncio
async def test_list_campaigns(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake() -> CampaignList:
        return CampaignList(campaigns=[_campaign()])

    monkeypatch.setattr("services.neurocomment.list_campaigns", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/neurocomment/campaigns")
    assert resp.status_code == 200
    assert [c["campaign_id"] for c in resp.json()["campaigns"]] == ["c1"]


@pytest.mark.asyncio
async def test_create_campaign(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(body: object) -> NeurocommentCampaign:  # noqa: ARG001
        return _campaign()

    monkeypatch.setattr("services.neurocomment.create_campaign", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/campaigns",
            json={"name": "Promo", "prompt": "mention the product"},
        )
    assert resp.status_code == 200
    assert resp.json()["campaign_id"] == "c1"


@pytest.mark.asyncio
async def test_board_found(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(campaign_id: str) -> NeurocommentBoard:
        return NeurocommentBoard(campaign_id=campaign_id, campaign_name="Promo", status="active")

    monkeypatch.setattr("services.neurocomment.load_neurocomment_board", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/neurocomment/campaigns/c1/board")
    assert resp.status_code == 200
    assert resp.json()["campaign_name"] == "Promo"


@pytest.mark.asyncio
async def test_board_missing_is_404(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _none(campaign_id: str) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("services.neurocomment.load_neurocomment_board", _none)
    async with _client(app) as client:
        resp = await client.get("/api/v1/neurocomment/campaigns/ghost/board")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_link_channel(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(campaign_id: str, channel: str) -> ChannelLinkOutcome:  # noqa: ARG001
        return ChannelLinkOutcome(status="linked", channel=channel)

    monkeypatch.setattr("services.neurocomment.link_channel", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/campaigns/c1/channels",
            json={"channel": "@news"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"status": "linked", "channel": "@news"}


@pytest.mark.asyncio
async def test_assign_account_is_204(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(campaign_id: str, account_id: str) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("services.neurocomment.assign_account_to_campaign", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/campaigns/c1/accounts",
            json={"account_id": "acc-1"},
        )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_start_runtime(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _start(listener_account_id: str) -> None:  # noqa: ARG001
        return None

    async def _status() -> NeurocommentRuntimeStatus:
        return NeurocommentRuntimeStatus(
            running=True,
            active_channels=2,
            listener_account_id="acc-1",
        )

    monkeypatch.setattr("services.neurocomment.start_neurocomment", _start)
    monkeypatch.setattr("services.neurocomment.neurocomment_runtime_status", _status)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/start",
            json={"listener_account_id": "acc-1"},
        )
    assert resp.status_code == 200
    assert resp.json()["running"] is True
