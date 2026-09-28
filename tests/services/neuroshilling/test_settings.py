"""The settings Save commits one campaign and dialogue snapshot."""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

import pytest

from core.repositories import neuroshilling as repository
from core.repositories.neuroshilling import _settings as settings_repository
from schemas.neuroshilling import NeuroshillingAccountAssignment, NeuroshillingCampaignUpdate
from schemas.neuroshilling_scenario import (
    NeuroshillingRoleInput,
    NeuroshillingScenarioUpdate,
    NeuroshillingSettingsUpdate,
    NeuroshillingStepInput,
)
from services import neuroshilling as ns_service
from services.neuroshilling import _runtime, engine, settings
from services.neuroshilling.campaigns import NeuroshillingConflictError
from tests.services.neuroshilling.helpers import seed_campaign

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

    from schemas.neuroshilling import NeuroshillingCampaign
    from tests.services.neuroshilling.helpers import Seeded


async def _body(seeded: Seeded, *, name: str = "Changed") -> NeuroshillingSettingsUpdate:
    campaign = await repository.fetch_campaign(seeded.campaign_id)
    assert campaign is not None
    fields = {
        key: getattr(campaign, key)
        for key in NeuroshillingCampaignUpdate.model_fields
        if key not in {"accounts", "expected_updated_at"}
    }
    fields["expected_updated_at"] = campaign.updated_at
    fields["name"] = name
    roster = await repository.list_campaign_accounts(seeded.campaign_id)
    fields["accounts"] = [
        NeuroshillingAccountAssignment(
            account_id=account.account_id,
            role_id=account.role_id,
            is_reserve=account.is_reserve,
        )
        for account in roster
    ]
    return NeuroshillingSettingsUpdate(
        campaign=NeuroshillingCampaignUpdate.model_validate(fields),
        scenario=NeuroshillingScenarioUpdate(
            roles=[
                NeuroshillingRoleInput(
                    role_id=role.role_id, name=role.name, description=role.description
                )
                for role in seeded.roles
            ],
            steps=[
                NeuroshillingStepInput.model_validate(
                    step.model_dump(exclude={"step_id", "position"})
                )
                for step in seeded.steps
            ],
        ),
    )


@pytest.mark.parametrize("stamp", ["junk", "2026-09-27T13:00:00", "x" * 41])
def test_settings_stamp_requires_a_bounded_timezone_aware_datetime(stamp: str) -> None:
    with pytest.raises(ValueError, match="expected_updated_at"):
        NeuroshillingCampaignUpdate(name="Promo", expected_updated_at=stamp)


@pytest.mark.asyncio
async def test_combined_save_commits_both_dirty_halves_and_drafts_approval() -> None:
    seeded = await seed_campaign(topic="delivery")
    body = await _body(seeded)
    body.campaign.topic = "new topic"
    body.campaign.targets_raw = "@alpha\n@beta"
    body.scenario.steps[0].text = "new dialogue"

    saved = await ns_service.save_settings(seeded.campaign_id, body)

    assert saved is not None
    assert saved.campaign.name == "Changed"
    assert saved.campaign.topic == "new topic"
    assert saved.campaign.targets_raw == "@alpha\n@beta"
    assert saved.scenario.steps[0].text == "new dialogue"
    assert saved.campaign.scenario_status == saved.scenario.scenario_status == "draft"


@pytest.mark.asyncio
async def test_settings_load_reads_one_snapshot_during_concurrent_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await seed_campaign(topic="delivery")
    body = await _body(seeded)
    body.scenario.steps[0].text = "new dialogue"
    body.campaign.accounts.pop()
    before_roster = await repository.list_campaign_accounts(seeded.campaign_id)
    entered, release = threading.Event(), threading.Event()
    original = settings_repository._select_roles

    def paused_roles(connection: Connection, campaign_id: str):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return original(connection, campaign_id)

    monkeypatch.setattr(settings_repository, "_select_roles", paused_roles)
    reading = asyncio.create_task(repository.load_settings(seeded.campaign_id))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 5), 6)
        saved = await asyncio.wait_for(ns_service.save_settings(seeded.campaign_id, body), 5)
        assert saved is not None
    finally:
        release.set()
    loaded = await asyncio.wait_for(reading, 5)
    assert loaded is not None
    assert loaded.campaign.updated_at == body.campaign.expected_updated_at
    assert loaded.scenario.steps[0].text == seeded.steps[0].text
    assert [account.account_id for account in loaded.accounts] == [
        account.account_id for account in before_roster
    ]
    assert [account.account_id for account in saved.accounts] == [
        body.campaign.accounts[0].account_id
    ]


@pytest.mark.asyncio
async def test_stale_settings_save_cannot_replace_another_edit() -> None:
    seeded = await seed_campaign(topic="delivery")
    first = await _body(seeded, name="First editor")
    stale = await _body(seeded, name="Second editor")
    stale.campaign.topic = "stale topic"

    saved = await ns_service.save_settings(seeded.campaign_id, first)
    assert saved is not None
    with pytest.raises(NeuroshillingConflictError, match="campaign_changed"):
        await ns_service.save_settings(seeded.campaign_id, stale)

    current = await repository.fetch_campaign(seeded.campaign_id)
    assert current is not None
    assert current.name == "First editor"
    assert current.topic == "delivery"


@pytest.mark.asyncio
async def test_settings_save_rejects_a_scenario_changed_since_the_form_loaded() -> None:
    seeded = await seed_campaign(topic="delivery")
    stale = await _body(seeded)
    changed_steps = [NeuroshillingStepInput(role_id=seeded.roles[0].role_id, text="new dialogue")]
    await repository.replace_scenario(
        seeded.campaign_id,
        [NeuroshillingRoleInput(role_id=role.role_id, name=role.name) for role in seeded.roles],
        changed_steps,
    )

    with pytest.raises(NeuroshillingConflictError, match="campaign_changed"):
        await ns_service.save_settings(seeded.campaign_id, stale)
    _, current_steps = await repository.load_scenario(seeded.campaign_id)
    assert [step.text for step in current_steps] == ["new dialogue"]


@pytest.mark.asyncio
async def test_settings_save_rejects_an_approval_changed_since_the_form_loaded() -> None:
    seeded = await seed_campaign(topic="delivery", approve=False)
    stale = await _body(seeded)
    approved = await repository.approve_scenario(
        seeded.campaign_id, expected_updated_at=stale.campaign.expected_updated_at
    )
    assert approved

    with pytest.raises(NeuroshillingConflictError, match="campaign_changed"):
        await ns_service.save_settings(seeded.campaign_id, stale)
    current = await repository.fetch_campaign(seeded.campaign_id)
    assert current is not None
    assert current.scenario_status == "approved"


@pytest.mark.asyncio
async def test_setup_only_save_keeps_an_unaffected_approval() -> None:
    seeded = await seed_campaign(topic="delivery")
    body = await _body(seeded)
    body.campaign.targets_raw = "@alpha\n@beta"

    saved = await ns_service.update_campaign(seeded.campaign_id, body.campaign)

    assert saved is not None
    assert saved.targets_raw == "@alpha\n@beta"
    assert saved.scenario_status == "approved"


@pytest.mark.asyncio
async def test_new_and_deleted_roles_rewire_the_roster_and_remain_stable_on_resave() -> None:
    seeded = await seed_campaign(topic="delivery")
    body = await _body(seeded)
    deleted_role = seeded.roles[1].role_id
    body.scenario.roles = [
        NeuroshillingRoleInput(role_id=seeded.roles[0].role_id, name="Kept"),
        NeuroshillingRoleInput(role_id="new-role", name="New"),
    ]
    body.scenario.steps = [
        NeuroshillingStepInput(role_id=seeded.roles[0].role_id, text="first"),
        NeuroshillingStepInput(role_id="new-role", text="second"),
    ]
    body.campaign.accounts[1].role_id = "new-role"

    saved = await ns_service.save_settings(seeded.campaign_id, body)

    assert saved is not None
    minted = saved.scenario.roles[1].role_id
    assert minted != "new-role"
    assert deleted_role not in {role.role_id for role in saved.scenario.roles}
    assert saved.scenario.steps[1].role_id == minted
    roster = await repository.list_campaign_accounts(seeded.campaign_id)
    assert roster[1].role_id == minted

    body.scenario.roles[1].role_id = minted
    body.scenario.steps[1].role_id = minted
    body.campaign.accounts[1].role_id = minted
    body.campaign.expected_updated_at = saved.campaign.updated_at
    saved_again = await ns_service.save_settings(seeded.campaign_id, body)
    assert saved_again is not None
    assert saved_again.scenario.roles[1].role_id == minted


@pytest.mark.asyncio
async def test_removed_role_clears_stale_roster_assignment() -> None:
    seeded = await seed_campaign(topic="delivery")
    body = await _body(seeded)
    body.scenario.roles.pop()
    body.scenario.steps.pop()

    saved = await ns_service.save_settings(seeded.campaign_id, body)

    assert saved is not None
    roster = await repository.list_campaign_accounts(seeded.campaign_id)
    assert roster[1].role_id is None


@pytest.mark.asyncio
async def test_failure_after_campaign_and_roles_rolls_everything_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await seed_campaign(topic="delivery")
    body = await _body(seeded)
    body.campaign.topic = "new topic"
    body.scenario.roles[0].name = "New role"
    before_campaign = await repository.fetch_campaign(seeded.campaign_id)
    before_roster = await repository.list_campaign_accounts(seeded.campaign_id)

    def failed_steps(*_args: object) -> None:
        reason = "injected failure"
        raise RuntimeError(reason)

    monkeypatch.setattr(settings_repository, "_write_steps", failed_steps)
    with pytest.raises(RuntimeError, match="injected failure"):
        await ns_service.save_settings(seeded.campaign_id, body)

    assert await repository.fetch_campaign(seeded.campaign_id) == before_campaign
    assert await repository.list_campaign_accounts(seeded.campaign_id) == before_roster
    roles, steps = await repository.load_scenario(seeded.campaign_id)
    assert roles == seeded.roles
    assert steps == seeded.steps


@pytest.mark.asyncio
async def test_start_refused_while_settings_validation_is_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await seed_campaign(topic="delivery")
    body = await _body(seeded)
    body.scenario.steps[0].text = "new dialogue"
    entered, release = asyncio.Event(), asyncio.Event()

    async def paused_accounts() -> set[str]:
        entered.set()
        await release.wait()
        return set(seeded.accounts)

    monkeypatch.setattr(settings, "_existing_account_ids", paused_accounts)
    saving = asyncio.create_task(ns_service.save_settings(seeded.campaign_id, body))
    await asyncio.wait_for(entered.wait(), 5)
    with pytest.raises(NeuroshillingConflictError, match="campaign_running"):
        await _runtime.start_campaign(seeded.campaign_id)
    release.set()
    saved = await asyncio.wait_for(saving, 5)
    assert saved is not None
    assert saved.scenario.scenario_status == "draft"
    with pytest.raises(NeuroshillingConflictError) as refusal:
        await _runtime.start_campaign(seeded.campaign_id)
    assert refusal.value.code == "scenario_not_approved"


@pytest.mark.asyncio
async def test_settings_refused_while_start_claimed_before_first_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await seed_campaign(topic="delivery")
    body = await _body(seeded)
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
    with pytest.raises(NeuroshillingConflictError, match="campaign_running"):
        await ns_service.save_settings(seeded.campaign_id, body)
    release.set()
    started = await asyncio.wait_for(starting, 5)
    assert started is not None
    assert started.status == "running"
    await _runtime.stop_campaign(seeded.campaign_id)
