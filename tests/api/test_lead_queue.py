"""Lead queue HTTP contract: add numbers, list them, remove one, settings and pause."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import lead_queue as repo
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI

_URL = "/api/v1/accounts/lead-queue"


@pytest.mark.asyncio
async def test_leads_are_added_listed_and_removed(app: FastAPI) -> None:
    async with _client(app) as client:
        added = await client.post(
            f"{_URL}/leads",
            json={
                "leads": [
                    {"phone": "+1 555 000-0001", "name": "Иван"},
                    {"phone": "15550000001"},
                    {"phone": "not a number"},
                    {"phone": "+15550000002"},
                ]
            },
        )
        assert added.status_code == 200
        assert added.json() == {"added": 2, "duplicates": 1, "invalid": 1}

        view = (await client.get(_URL, params={"limit": 10})).json()
        assert view["counts"] == {"pending": 2}
        assert [(i["phone"], i["client_name"]) for i in view["items"]] == [
            ("+15550000002", None),
            ("+15550000001", "Иван"),
        ]
        assert view["settings"]["paused"] is True

        newest = view["items"][0]
        lead = await repo.get_lead(view["items"][1]["id"])
        assert lead is not None
        lead.status = "messaging"
        await repo.save_leads([lead])
        assert (await client.delete(f"{_URL}/leads/{newest['id']}")).status_code == 204
        busy = await client.delete(f"{_URL}/leads/{lead.id}")
        assert busy.status_code == 409
        assert (await client.delete(f"{_URL}/leads/{newest['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_settings_save_and_refuse_a_running_queue_without_a_message(app: FastAPI) -> None:
    async with _client(app) as client:
        refused = await client.put(
            f"{_URL}/settings", json={"account_ids": ["a1"], "text": " ", "paused": False}
        )
        assert refused.status_code == 422

        saved = await client.put(
            f"{_URL}/settings", json={"account_ids": ["a1"], "text": "Hi", "paused": False}
        )
        assert saved.status_code == 200
        body = saved.json()
        assert (body["account_ids"], body["text"], body["paused"]) == (["a1"], "Hi", False)
        assert body["owner_user_id"]
        assert (await client.get(_URL)).json()["settings"] == body
