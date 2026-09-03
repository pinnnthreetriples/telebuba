"""Validator tests for the advanced search request.

Own file: ``test_neurocomment_discovery`` is near the 700-line test cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from schemas.neurocomment_discovery import DiscoverySearchOutcome
from schemas.neurocomment_discovery_request import DiscoverySearchRequest

if TYPE_CHECKING:
    from fastapi import FastAPI

_SEARCH = "/api/v1/neurocomment/campaigns/c1/discovery/search"


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({"keywords": ["crypto"]}, "account_ids is required: the server no longer picks"),
        ({"keywords": ["crypto"], "account_ids": []}, "an empty pick is no pick"),
        ({"keywords": ["crypto"], "account_ids": [" "]}, "a blank pick is no pick either"),
        ({"keywords": [], "account_ids": ["a"]}, "no keywords and category any"),
        ({"account_ids": ["a"], "category": "any"}, "category any alone is nothing to search"),
        (
            {"keywords": ["crypto"], "account_ids": ["a"], "kind": "groups", "comments": "on"},
            "groups have no comment verdict",
        ),
        (
            {
                "keywords": ["crypto"],
                "account_ids": ["a"],
                "kind": "groups",
                "access": "subscription",
            },
            "recommendations return channels only",
        ),
        ({"keywords": ["crypto"], "account_ids": ["a"], "limit": 0}, "limit below the floor"),
        ({"keywords": ["crypto"], "account_ids": ["a"], "limit": 501}, "limit above the cap"),
        ({"keywords": ["crypto"], "account_ids": ["a"], "language": "tr"}, "unknown language"),
        ({"keywords": ["crypto"], "account_ids": ["a"], "category": "cats"}, "unknown category"),
        (
            {"keywords": ["crypto"], "account_ids": [f"a{index}" for index in range(11)]},
            "more accounts than one run may hold",
        ),
    ],
)
@pytest.mark.asyncio
async def test_unusable_requests_are_422(app: FastAPI, body: dict, reason: str) -> None:
    async with _client(app) as client:
        resp = await client.post(_SEARCH, json=body)

    assert resp.status_code == 422, reason


@pytest.mark.asyncio
async def test_a_category_alone_is_enough_to_search(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bundle stands in for typed keywords; the service sees the code, not the words."""
    seen: list[DiscoverySearchRequest] = []

    async def _fake(campaign_id: str, body: DiscoverySearchRequest) -> DiscoverySearchOutcome:  # noqa: ARG001
        seen.append(body)
        return DiscoverySearchOutcome(status="started")

    monkeypatch.setattr("services.neurocomment.start_discovery", _fake)
    async with _client(app) as client:
        resp = await client.post(
            _SEARCH,
            json={"category": "crypto", "account_ids": ["acc-1"], "kind": "all", "limit": 50},
        )

    assert resp.status_code == 202
    assert seen[0].keywords == []
    assert seen[0].category == "crypto"
    assert seen[0].kind == "all"
    assert seen[0].limit == 50
    assert seen[0].hide_seen is True


def test_account_ids_are_stripped_and_deduped_in_order() -> None:
    request = DiscoverySearchRequest(keywords=["crypto"], account_ids=[" b ", "a", "b", "", "a"])

    assert request.account_ids == ["b", "a"]


def test_defaults_are_the_permissive_channel_search() -> None:
    request = DiscoverySearchRequest(keywords=["crypto"], account_ids=["a"])

    assert (request.kind, request.category, request.language) == ("channels", "any", "any")
    assert (request.comments, request.access, request.hide_seen) == ("any", "any", True)
    assert request.limit == 200
