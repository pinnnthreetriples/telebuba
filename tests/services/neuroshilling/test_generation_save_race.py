"""A long generation must not overwrite a settings Save that landed during the ask."""

from __future__ import annotations

import asyncio

import pytest

from core.repositories import neuroshilling as repository
from schemas.neuroshilling import NeuroshillingAccountAssignment, NeuroshillingCampaignUpdate
from schemas.neuroshilling_scenario import (
    NeuroshillingGenerateRequest,
    NeuroshillingRoleInput,
    NeuroshillingScenarioUpdate,
    NeuroshillingSettingsUpdate,
    NeuroshillingStepInput,
)
from services import neuroshilling as ns_service
from services.neuroshilling import scenario
from tests.services.neuroshilling.helpers import seed_campaign


@pytest.mark.asyncio
async def test_generation_refuses_to_replace_a_settings_save_landing_during_the_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await seed_campaign(topic="delivery", approve=False)
    entered, release = asyncio.Event(), asyncio.Event()

    async def paused_ask(*_args: object) -> NeuroshillingScenarioUpdate:
        entered.set()
        await asyncio.wait_for(release.wait(), 5)
        return NeuroshillingScenarioUpdate(
            roles=[NeuroshillingRoleInput(role_id=seeded.roles[0].role_id, name="AI role")],
            steps=[NeuroshillingStepInput(role_id=seeded.roles[0].role_id, text="AI text")],
        )

    monkeypatch.setattr(scenario, "_ask", paused_ask)
    generating = asyncio.create_task(
        ns_service.generate_scenario(seeded.campaign_id, NeuroshillingGenerateRequest())
    )
    await asyncio.wait_for(entered.wait(), 5)
    current = await repository.fetch_campaign(seeded.campaign_id)
    assert current is not None
    roster = await repository.list_campaign_accounts(seeded.campaign_id)
    campaign_fields = {
        key: getattr(current, key)
        for key in NeuroshillingCampaignUpdate.model_fields
        if key not in {"accounts", "expected_updated_at"}
    }
    saved = await ns_service.save_settings(
        seeded.campaign_id,
        NeuroshillingSettingsUpdate(
            campaign=NeuroshillingCampaignUpdate(
                **campaign_fields,
                expected_updated_at=current.updated_at,
                accounts=[
                    NeuroshillingAccountAssignment(
                        account_id=account.account_id,
                        role_id=account.role_id,
                        is_reserve=account.is_reserve,
                    )
                    for account in roster
                ],
            ),
            scenario=NeuroshillingScenarioUpdate(
                roles=[
                    NeuroshillingRoleInput(role_id=role.role_id, name=role.name)
                    for role in seeded.roles
                ],
                steps=[
                    NeuroshillingStepInput(role_id=seeded.roles[0].role_id, text="operator text")
                ],
            ),
        ),
    )
    assert saved is not None
    release.set()
    with pytest.raises(ns_service.NeuroshillingConflictError, match="campaign_changed"):
        await asyncio.wait_for(generating, 5)
    _, stored_steps = await repository.load_scenario(seeded.campaign_id)
    assert [step.text for step in stored_steps] == ["operator text"]
