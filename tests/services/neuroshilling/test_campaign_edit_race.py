"""Start and campaign edits share one in-process single-flight fence."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingCampaign

from core.repositories import neuroshilling as repository
from schemas.neuroshilling import NeuroshillingAccountAssignment, NeuroshillingCampaignUpdate
from schemas.neuroshilling_scenario import (
    NeuroshillingGenerateRequest,
    NeuroshillingScenarioUpdate,
)
from services import neuroshilling as ns_service
from services.neuroshilling import _runtime, campaigns, engine
from services.neuroshilling.campaigns import NeuroshillingConflictError
from tests.services.neuroshilling.helpers import seed_campaign


@pytest.mark.asyncio
@pytest.mark.parametrize("edit", ["update", "scenario", "approve", "generate", "delete"])
async def test_edit_refused_while_start_is_between_claim_and_status_write(
    edit: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await seed_campaign(topic="delivery")
    entered, release = asyncio.Event(), asyncio.Event()
    original = _runtime._check_roster

    async def paused_roster(campaign: NeuroshillingCampaign) -> list[str]:
        entered.set()
        await release.wait()
        return await original(campaign)

    async def held_run(_campaign_id: str, _run_id: str) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(_runtime, "_check_roster", paused_roster)
    monkeypatch.setattr(engine, "run_campaign", held_run)
    starting = asyncio.create_task(_runtime.start_campaign(seeded.campaign_id))
    await asyncio.wait_for(entered.wait(), 5)
    campaign = await repository.fetch_campaign(seeded.campaign_id)
    assert campaign is not None
    assert campaign.status == "idle"
    stamp = campaign.updated_at

    async def perform_edit() -> None:
        if edit == "update":
            await ns_service.update_campaign(
                seeded.campaign_id,
                NeuroshillingCampaignUpdate(expected_updated_at=stamp, name="Changed"),
            )
        elif edit == "scenario":
            await ns_service.set_scenario(
                seeded.campaign_id, NeuroshillingScenarioUpdate(roles=[], steps=[])
            )
        elif edit == "approve":
            await ns_service.approve_scenario(seeded.campaign_id)
        elif edit == "generate":
            await ns_service.generate_scenario(seeded.campaign_id, NeuroshillingGenerateRequest())
        else:
            await ns_service.delete_campaign(seeded.campaign_id)

    with pytest.raises(NeuroshillingConflictError, match="campaign_running"):
        await perform_edit()

    release.set()
    started = await asyncio.wait_for(starting, 5)
    assert started is not None
    assert started.status == "running"
    campaign = await repository.fetch_campaign(seeded.campaign_id)
    assert campaign is not None
    assert campaign.name == "Promo"
    assert campaign.scenario_status == "approved"
    await _runtime.stop_campaign(seeded.campaign_id)


@pytest.mark.asyncio
async def test_start_refused_while_an_update_is_awaiting_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await seed_campaign(topic="delivery")
    campaign = await repository.fetch_campaign(seeded.campaign_id)
    assert campaign is not None
    entered, release = asyncio.Event(), asyncio.Event()

    async def paused_accounts() -> set[str]:
        entered.set()
        await release.wait()
        return set(seeded.accounts)

    monkeypatch.setattr(campaigns, "_existing_account_ids", paused_accounts)
    changing = asyncio.create_task(
        ns_service.update_campaign(
            seeded.campaign_id,
            NeuroshillingCampaignUpdate(
                expected_updated_at=campaign.updated_at,
                name="Changed",
                topic="delivery",
                targets_raw="@alpha",
                accounts=[
                    NeuroshillingAccountAssignment(account_id=account_id, role_id=role.role_id)
                    for account_id, role in zip(seeded.accounts, seeded.roles, strict=True)
                ],
            ),
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    with pytest.raises(NeuroshillingConflictError, match="campaign_running"):
        await _runtime.start_campaign(seeded.campaign_id)
    release.set()
    updated = await asyncio.wait_for(changing, 5)
    assert updated is not None
    assert updated.name == "Changed"

    async def held_run(_campaign_id: str, _run_id: str) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(engine, "run_campaign", held_run)
    started = await _runtime.start_campaign(seeded.campaign_id)
    assert started is not None
    assert started.status == "running"
    await _runtime.stop_campaign(seeded.campaign_id)


@pytest.mark.asyncio
async def test_cancelled_start_releases_claim_before_first_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await seed_campaign(topic="delivery")
    entered = asyncio.Event()
    original = repository.fetch_campaign

    async def paused_fetch(campaign_id: str) -> NeuroshillingCampaign | None:
        entered.set()
        await asyncio.Event().wait()
        return await original(campaign_id)

    monkeypatch.setattr(repository, "fetch_campaign", paused_fetch)
    starting = asyncio.create_task(_runtime.start_campaign(seeded.campaign_id))
    await asyncio.wait_for(entered.wait(), 5)
    with pytest.raises(NeuroshillingConflictError, match="campaign_running"):
        await ns_service.delete_campaign(seeded.campaign_id)
    starting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await starting
    monkeypatch.setattr(repository, "fetch_campaign", original)
    assert await ns_service.delete_campaign(seeded.campaign_id)


@pytest.mark.asyncio
async def test_cancelled_update_releases_edit_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    seeded = await seed_campaign(topic="delivery")
    campaign = await repository.fetch_campaign(seeded.campaign_id)
    assert campaign is not None
    entered = asyncio.Event()

    async def paused_accounts() -> set[str]:
        entered.set()
        await asyncio.Event().wait()
        return set(seeded.accounts)

    monkeypatch.setattr(campaigns, "_existing_account_ids", paused_accounts)
    changing = asyncio.create_task(
        ns_service.update_campaign(
            seeded.campaign_id,
            NeuroshillingCampaignUpdate(expected_updated_at=campaign.updated_at, name="Changed"),
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    changing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await changing

    async def held_run(_campaign_id: str, _run_id: str) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(engine, "run_campaign", held_run)
    started = await _runtime.start_campaign(seeded.campaign_id)
    assert started is not None
    assert started.status == "running"
    await _runtime.stop_campaign(seeded.campaign_id)
