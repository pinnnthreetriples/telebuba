"""The account card's additive spam fields and the fleet budget read (T14, docs/spam-intel.md)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.repositories.spam_probe_ledger import start_attempt
from schemas.accounts import AccountCreate
from schemas.spam_intel import ProbeAttemptStart
from services import spam_intel
from services.accounts import add_account
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI


async def _probe(account_id: str, at: datetime, purpose: str = "routine") -> None:
    await start_attempt(
        ProbeAttemptStart(
            account_id=account_id,
            purpose=purpose,  # ty: ignore[invalid-argument-type]
            caller="test",
            started_at=at.isoformat(),
        )
    )


@pytest.mark.asyncio
async def test_the_accounts_list_carries_the_card_fields(app: FastAPI) -> None:
    now = datetime.now(UTC)
    flood_at = now - timedelta(hours=2)
    await add_account(AccountCreate(account_id="acc-1"))
    await add_account(AccountCreate(account_id="acc-2"))
    await spam_intel.record_evidence(
        "acc-1", "peer_flood_send", source="execute", observed_at=flood_at
    )
    await spam_intel.record_evidence("acc-1", "tos_restricted", source="session_check", detail="x")
    await _probe("acc-1", now - timedelta(hours=3))
    await _probe("acc-1", now - timedelta(days=2))

    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts")

    rows = {item["account_id"]: item for item in resp.json()["items"]}
    flooded, untouched = rows["acc-1"], rows["acc-2"]
    assert (flooded["spam_verdict"], flooded["spam_source"]) == ("limited", "peer_flood")
    assert flooded["spam_confidence"] in {"high", "medium", "low"}
    assert datetime.fromisoformat(flooded["spam_observed_at"]) == flood_at
    assert flooded["spam_valid_until"] is None  # an undated limit is "indefinitely"
    assert flooded["spam_probes_7d"] == 2
    assert flooded["spam_last_probe_at"] is not None
    assert flooded["spam_out_of_turn"] is True  # the last probe was 3 hours ago
    # The platform restriction is its own flag with its reason, beside the verdict.
    assert (flooded["spam_tos_restricted"], flooded["spam_tos_reason"]) == (True, "x")
    assert flooded["spam_appeal_status"] == "none"
    assert (untouched["spam_verdict"], untouched["spam_source"]) == ("unknown", "none")
    assert (untouched["spam_probes_7d"], untouched["spam_out_of_turn"]) == (0, False)
    assert untouched["spam_tos_restricted"] is False


@pytest.mark.asyncio
async def test_the_budget_read_reports_the_window_and_the_caps(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.spam_intel, "fleet_soft_cap", 2)
    monkeypatch.setattr(settings.spam_intel, "fleet_hard_cap", 5)
    monkeypatch.setattr(settings.spam_intel, "out_of_turn_warn_count", 1)
    now = datetime.now(UTC)
    await add_account(AccountCreate(account_id="acc-1"))
    await _probe("acc-1", now - timedelta(hours=1), "operator")
    await _probe("acc-1", now - timedelta(hours=2))
    await _probe("acc-1", now - timedelta(hours=30))  # outside the 24 h window

    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/spam-budget")

    assert resp.status_code == 200
    assert resp.json() == {
        "probes_24h": 2,
        "soft_cap": 2,
        "hard_cap": 5,
        "out_of_turn_24h": 1,
        "out_of_turn_warn_at": 1,
        "daily_cap_reached": True,
        "out_of_turn_warned": True,
    }


@pytest.mark.asyncio
async def test_a_quiet_fleet_raises_no_warning(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/spam-budget")
    body = resp.json()
    assert (body["probes_24h"], body["daily_cap_reached"], body["out_of_turn_warned"]) == (
        0,
        False,
        False,
    )
