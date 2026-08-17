"""The window between the approval verdict and the write that records it.

``approve_scenario`` reads the campaign and the dialogue, then spends up to one live
Telegram read per account of the media step's role before it writes. A save landing in
there used to be stamped ``approved`` anyway — the verdict belonged to text nobody was
storing any more — which is the one bug the whole gate exists to prevent.

Driven with explicit gates. A ``gather`` cannot produce this order: the save has to
arrive after the verdict is reached and before the write lands, and it has fewer
suspension points ahead of it than the approval has behind it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.repositories import neuroshilling as repository
from schemas.neuroshilling import NeuroshillingCampaignCreate
from schemas.neuroshilling_scenario import (
    NeuroshillingRoleInput,
    NeuroshillingScenarioUpdate,
    NeuroshillingStepInput,
)
from services import neuroshilling as ns_service
from services.neuroshilling import scenario

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingCampaign

# One gate waits for a peer a few in-process reads away; the other for a write.
_GATE_SECONDS = 0.3


async def _campaign() -> NeuroshillingCampaign:
    return await repository.create_campaign(NeuroshillingCampaignCreate(name="Promo"))


def _approvable() -> NeuroshillingScenarioUpdate:
    return NeuroshillingScenarioUpdate(
        roles=[NeuroshillingRoleInput(role_id="a", name="Skeptic")],
        steps=[NeuroshillingStepInput(role_id="a", text="first")],
    )


def _unapprovable() -> NeuroshillingScenarioUpdate:
    """A dialogue the gate refuses: a line with nobody cast to say it."""
    return NeuroshillingScenarioUpdate(
        roles=[NeuroshillingRoleInput(role_id="a", name="Skeptic")],
        steps=[NeuroshillingStepInput(role_id=None, text="who says this")],
    )


@pytest.mark.asyncio
async def test_a_save_landing_mid_approval_leaves_the_campaign_a_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verdict was reached on the old text, so it may not be recorded on the new.

    The save is gated into the media-check await — the long one, N live reads wide — and
    replaces the dialogue with one the gate refuses. Written unconditionally, the
    approval landed on top of it and the campaign was launchable with a step no account
    was cast for. The write now names the ``updated_at`` the verdict read, and the save
    moved it.
    """
    campaign = await _campaign()
    await ns_service.set_scenario(campaign.campaign_id, _approvable())
    verdict_reached = asyncio.Event()
    saved = asyncio.Event()

    async def _gate_before_the_write(*_args: object) -> None:
        verdict_reached.set()
        await asyncio.wait_for(saved.wait(), timeout=_GATE_SECONDS)

    monkeypatch.setattr(scenario, "_refuse_unreachable_media", _gate_before_the_write)

    async def _save_inside_the_window() -> None:
        await asyncio.wait_for(verdict_reached.wait(), timeout=_GATE_SECONDS)
        try:
            await ns_service.set_scenario(campaign.campaign_id, _unapprovable())
        finally:
            saved.set()

    approved, _saved = await asyncio.gather(
        ns_service.approve_scenario(campaign.campaign_id),
        _save_inside_the_window(),
    )

    assert approved is not None
    # Answered with what is stored, which is the draft the other request wrote.
    assert approved.scenario_status == "draft"
    stored = await repository.fetch_campaign(campaign.campaign_id)
    assert stored is not None
    assert stored.scenario_status == "draft"
    # And the text that ended up stored really is text the gate refuses, which is what
    # makes the status above the whole difference between a launchable campaign and not.
    with pytest.raises(ns_service.NeuroshillingInvalidError) as refusal:
        await ns_service.approve_scenario(campaign.campaign_id)
    assert refusal.value.code == "scenario_invalid"


@pytest.mark.asyncio
async def test_an_undisturbed_approval_still_lands() -> None:
    """The conditional write must not refuse the ordinary case it is guarding."""
    campaign = await _campaign()
    await ns_service.set_scenario(campaign.campaign_id, _approvable())

    approved = await ns_service.approve_scenario(campaign.campaign_id)

    assert approved is not None
    assert approved.scenario_status == "approved"
