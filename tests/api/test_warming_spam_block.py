"""``/warming/spam-block/keep`` («Оставить») and ``/warming/waves/{id}`` (the wave history)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from core.db import create_account, upsert_warming_state
from core.repositories.lifecycle import append_lifecycle_event
from schemas.accounts import AccountCreate
from schemas.lifecycle import LifecycleSnapshot
from schemas.warming import WarmingStateWrite

if TYPE_CHECKING:
    from fastapi import FastAPI

_START = "2026-10-01T00:00:00+00:00"


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_keep_answers_the_idle_card_and_refuses_a_stale_one(app: FastAPI) -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="spam_blocked", spam_blocked_at=_START)
    )
    await create_account(AccountCreate(account_id="acc-2"))

    async with _client(app) as client:
        kept = await client.post("/api/v1/warming/spam-block/keep", json={"account_id": "acc-1"})
        stale = await client.post("/api/v1/warming/spam-block/keep", json={"account_id": "acc-2"})
        ghost = await client.post("/api/v1/warming/spam-block/keep", json={"account_id": "nope"})

    assert kept.status_code == 200
    assert (kept.json()["state"], kept.json()["spam_blocked_at"]) == ("idle", _START)
    assert stale.status_code == 409
    assert stale.json()["error"]["message"] == "not_spam_blocked"
    assert ghost.status_code == 404


@pytest.mark.asyncio
async def test_the_wave_history_of_an_account(app: FastAPI) -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="sleeping", started_at=_START, warmed_days=2)
    )
    await append_lifecycle_event(
        "acc-1",
        "warming_started",
        {"wave": 1, "target_days": 14, "persona": "calm"},
        LifecycleSnapshot(stint_started_at=_START),
    )

    async with _client(app) as client:
        found = await client.get("/api/v1/warming/waves/acc-1")
        missing = await client.get("/api/v1/warming/waves/nope")

    assert found.status_code == 200
    assert found.json() == {
        "account_id": "acc-1",
        "waves": [
            {
                "wave": 1,
                "started_at": _START,
                "ended_at": None,
                "end_reason": None,
                "worked_days": 2,
                "target_days": 14,
                "persona": "calm",
            }
        ],
    }
    assert missing.status_code == 404
