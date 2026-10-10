"""``/chat-broadcast/collections`` — saved chat lists, against the real service."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

_BASE = "/api/v1/chat-broadcast/collections"


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_lists_are_created_normalised_replaced_and_deleted(app: FastAPI) -> None:
    async with _client(app) as client:
        created = await client.post(
            _BASE, json={"name": " Crypto ", "targets": ["@alpha", "t.me/alpha", "@beta"]}
        )
        assert created.status_code == 200
        body = created.json()
        # One link per chat, as a campaign keeps them; the name is trimmed.
        assert (body["name"], body["targets"]) == ("Crypto", ["@alpha", "@beta"])
        collection_id = body["collection_id"]

        saved = await client.put(
            f"{_BASE}/{collection_id}", json={"name": "Coins", "targets": ["@gamma"]}
        )
        assert saved.status_code == 200
        assert saved.json()["targets"] == ["@gamma"]

        listed = await client.get(_BASE)
        assert [(c["name"], c["targets"]) for c in listed.json()["items"]] == [
            ("Coins", ["@gamma"])
        ]

        assert (await client.delete(f"{_BASE}/{collection_id}")).status_code == 204
        assert (await client.delete(f"{_BASE}/{collection_id}")).status_code == 404
        assert (await client.get(_BASE)).json()["items"] == []


@pytest.mark.asyncio
async def test_refusals_carry_their_status_and_code(app: FastAPI) -> None:
    async with _client(app) as client:
        first = await client.post(_BASE, json={"name": "Crypto", "targets": []})
        assert first.status_code == 200

        taken = await client.post(_BASE, json={"name": "Crypto", "targets": []})
        assert taken.status_code == 409
        assert taken.json()["error"]["message"] == "collection_name_taken"

        invalid = await client.post(_BASE, json={"name": "Bad", "targets": ["not a link !!"]})
        assert invalid.status_code == 400
        assert invalid.json()["error"]["message"] == "invalid_target"

        missing = await client.put(f"{_BASE}/nope", json={"name": "X", "targets": []})
        assert missing.status_code == 404

        empty = await client.post(_BASE, json={"name": "", "targets": []})
        assert empty.status_code == 422
