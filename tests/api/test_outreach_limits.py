"""Outreach-limits HTTP contract: read the defaults, save, reject a bad range."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

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

        body.pop("updated_at")
        saved = await client.put(_URL, json={**body, "contact_imports_per_day": 10})
        assert saved.status_code == 200
        assert saved.json()["contact_imports_per_day"] == 10
        assert (await client.get(_URL)).json() == saved.json()

        rejected = await client.put(
            _URL, json={**body, "lookup_min_delay_seconds": 900, "lookup_max_delay_seconds": 60}
        )
        assert rejected.status_code == 422
        assert (await client.get(_URL)).json() == saved.json()
