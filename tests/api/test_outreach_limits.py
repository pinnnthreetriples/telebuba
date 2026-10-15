"""Outreach HTTP contract: limits, today's usage per account and saved presets."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from core.repositories.outreach import add_outreach_usage
from schemas.outreach_limits import OutreachDailyUsage
from schemas.outreach_presets import MAX_PRESETS
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI

_URL = "/api/v1/accounts/outreach-limits"


@pytest.mark.asyncio
async def test_outreach_limits_read_save_and_validate(app: FastAPI) -> None:
    async with _client(app) as client:
        defaults = await client.get(_URL)
        assert defaults.status_code == 200
        body = defaults.json()
        assert body["contact_imports_per_day"] == 15
        assert body["first_messages_per_day"] == 25
        assert body["updated_at"] is None
        assert body["recipients"] == {
            "no_repeat_days": 30,
            "one_account_per_client": True,
            "recheck_not_found": True,
        }

        body.pop("updated_at")
        rules = {"no_repeat_days": None, "one_account_per_client": False, "recheck_not_found": True}
        saved = await client.put(
            _URL, json={**body, "contact_imports_per_day": 10, "recipients": rules}
        )
        assert saved.status_code == 200
        assert saved.json()["contact_imports_per_day"] == 10
        assert saved.json()["recipients"] == rules
        assert (await client.get(_URL)).json() == saved.json()

        rejected = await client.put(
            _URL, json={**body, "lookup_min_delay_seconds": 900, "lookup_max_delay_seconds": 60}
        )
        assert rejected.status_code == 422
        assert (await client.get(_URL)).json() == saved.json()


_PRESETS = "/api/v1/accounts/outreach-presets"
_VALUES = {
    "pause_seconds": 150,
    "first_messages_per_day": 22,
    "contact_imports_per_day": 14,
    "lookup_batch_size": 3,
}


@pytest.mark.asyncio
async def test_outreach_presets_crud(app: FastAPI) -> None:
    async with _client(app) as client:
        assert (await client.get(_PRESETS)).json() == {"items": []}

        created = await client.post(_PRESETS, json={"name": "Центр", **_VALUES})
        assert created.status_code == 201
        preset = created.json()
        assert preset["name"] == "Центр"

        taken = await client.post(_PRESETS, json={"name": "центр", **_VALUES})
        assert taken.status_code == 400
        assert taken.json()["error"]["message"] == "preset_name_taken"

        renamed = await client.put(
            f"{_PRESETS}/{preset['id']}", json={"name": "Филиал", **_VALUES, "pause_seconds": 60}
        )
        assert renamed.status_code == 200
        assert (renamed.json()["name"], renamed.json()["pause_seconds"]) == ("Филиал", 60)
        missing = await client.put(f"{_PRESETS}/nope", json={"name": "x", **_VALUES})
        assert missing.status_code == 404

        assert (await client.delete(f"{_PRESETS}/{preset['id']}")).status_code == 204
        assert (await client.delete(f"{_PRESETS}/{preset['id']}")).status_code == 404
        assert (await client.get(_PRESETS)).json() == {"items": []}


@pytest.mark.asyncio
async def test_saving_more_than_the_preset_limit_is_refused(app: FastAPI) -> None:
    async with _client(app) as client:
        for index in range(MAX_PRESETS):
            response = await client.post(_PRESETS, json={"name": f"p{index}", **_VALUES})
            assert response.status_code == 201
        refused = await client.post(_PRESETS, json={"name": "one more", **_VALUES})
        assert refused.status_code == 400
        assert refused.json()["error"]["message"] == "too_many_presets"


@pytest.mark.asyncio
async def test_usage_today_lists_each_requested_account(app: FastAPI) -> None:
    day = datetime.now(UTC).date().isoformat()
    await add_outreach_usage(
        OutreachDailyUsage(account_id="a1", day=day, contact_imports=6, first_messages=2)
    )
    await add_outreach_usage(
        OutreachDailyUsage(account_id="a1", day="2020-01-01", contact_imports=9)
    )
    async with _client(app) as client:
        response = await client.get(
            "/api/v1/accounts/outreach-usage", params={"account_ids": ["a1", "a2", "a1"]}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["day"] == day
    assert [
        (i["account_id"], i["contact_imports"], i["first_messages"]) for i in body["items"]
    ] == [
        ("a1", 6, 2),
        ("a2", 0, 0),
    ]
