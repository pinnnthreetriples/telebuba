"""The account picker behind the channel-discovery search form — a thin route."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from api import create_app
from schemas.neurocomment_discovery_request import DiscoveryAccountList, DiscoveryAccountOption

if TYPE_CHECKING:
    from fastapi import FastAPI

_ACCOUNTS = "/api/v1/neurocomment/discovery/accounts"


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_list_accounts_serializes_the_picker(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake() -> DiscoveryAccountList:
        return DiscoveryAccountList(
            items=[
                DiscoveryAccountOption(account_id="a1", name="Paid", username="paid", premium=True),
                DiscoveryAccountOption(
                    account_id="a2",
                    name="Warm",
                    busy_reason="account_busy",
                ),
            ],
        )

    monkeypatch.setattr("services.neurocomment.list_search_accounts", _fake)
    async with _client(app) as client:
        resp = await client.get(_ACCOUNTS)

    assert resp.status_code == 200
    assert resp.json() == {
        "items": [
            {
                "account_id": "a1",
                "name": "Paid",
                "username": "paid",
                "premium": True,
                "busy_reason": None,
            },
            {
                "account_id": "a2",
                "name": "Warm",
                "username": None,
                "premium": None,
                "busy_reason": "account_busy",
            },
        ],
    }


@pytest.mark.asyncio
async def test_list_accounts_requires_authentication() -> None:
    async with _client(create_app()) as client:
        resp = await client.get(_ACCOUNTS)

    assert resp.status_code == 401
