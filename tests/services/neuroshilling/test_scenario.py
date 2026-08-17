"""Scenario policy: the approval gate, what resets it, and what a save may declare.

The gate is the reason this module exists. ``scenario_status='approved'`` is what a
later stage checks before it publishes text into other people's chats, so every
path that could leave an approval standing over changed words is pinned here.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError
from sqlalchemy import update as sql_update

from core.config import settings
from core.db import _get_engine, create_account
from core.repositories import neuroshilling as repository
from core.repositories.neuroshilling._tables import _neuroshilling_campaigns
from schemas.accounts import AccountCreate
from schemas.neuroshilling import (
    NeuroshillingAccountAssignment,
    NeuroshillingCampaignCreate,
    NeuroshillingCampaignUpdate,
)
from schemas.neuroshilling_scenario import (
    NeuroshillingGenerateRequest,
    NeuroshillingRole,
    NeuroshillingRoleInput,
    NeuroshillingScenarioUpdate,
    NeuroshillingStep,
    NeuroshillingStepInput,
)
from services import neuroshilling as ns_service
from services.neuroshilling import _generate, _state, scenario

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingCampaign


async def _campaign(**fields: Any) -> NeuroshillingCampaign:
    campaign = await repository.create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    if fields:
        updated = await repository.update_campaign(
            campaign.campaign_id,
            NeuroshillingCampaignUpdate(name="Promo", **fields),
        )
        assert updated is not None
        return updated
    return campaign


def _dialogue(**overrides: Any) -> NeuroshillingScenarioUpdate:
    return NeuroshillingScenarioUpdate(
        roles=[NeuroshillingRoleInput(role_id="a", name="Skeptic")],
        steps=[
            NeuroshillingStepInput(role_id="a", text="first"),
            NeuroshillingStepInput(role_id="a", text="second", reply_to_position=1),
        ],
        **overrides,
    )


async def _approved(**fields: Any) -> NeuroshillingCampaign:
    campaign = await _campaign(topic="delivery", **fields)
    await ns_service.set_scenario(campaign.campaign_id, _dialogue())
    scenario_read = await ns_service.approve_scenario(campaign.campaign_id)
    assert scenario_read is not None
    assert scenario_read.scenario_status == "approved"
    stored = await repository.fetch_campaign(campaign.campaign_id)
    assert stored is not None
    return stored


async def _set_status(campaign_id: str, status: str) -> None:
    def _write() -> None:
        with _get_engine().begin() as connection:
            connection.execute(
                sql_update(_neuroshilling_campaigns)
                .where(_neuroshilling_campaigns.c.campaign_id == campaign_id)
                .values(status=status),
            )

    await asyncio.to_thread(_write)


def test_a_step_may_not_declare_a_reversed_delay() -> None:
    """``min <= max`` is OUR rule, not pydantic's, and this is its only test."""
    with pytest.raises(ValidationError):
        NeuroshillingStepInput(role_id="a", text="hi", delay_min_seconds=300, delay_max_seconds=10)


def test_two_roles_may_not_share_one_key() -> None:
    """One key names ONE role, or the write forks into an update and an insert.

    Keyless roles are exempt and stay exempt: the server mints an id for each, so
    several of them in one body is the ordinary case for a scenario typed from
    scratch.
    """
    with pytest.raises(ValidationError):
        NeuroshillingScenarioUpdate(
            roles=[
                NeuroshillingRoleInput(role_id="a", name="Skeptic"),
                NeuroshillingRoleInput(role_id="a", name="Regular"),
            ],
        )

    keyless = NeuroshillingScenarioUpdate(
        roles=[NeuroshillingRoleInput(name="Skeptic"), NeuroshillingRoleInput(name="Regular")],
    )

    assert len(keyless.roles) == 2


def test_the_budget_window_forgets_calls_older_than_a_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neuroshilling, "max_llm_calls_per_day", 1)
    _state.record_llm_call(datetime.now(UTC) - timedelta(hours=25))

    assert _state.at_daily_llm_cap() is False


@pytest.mark.asyncio
async def test_an_unknown_campaign_has_no_scenario() -> None:
    assert await ns_service.load_scenario("nope") is None
    assert await ns_service.set_scenario("nope", _dialogue()) is None
    assert await ns_service.approve_scenario("nope") is None
    assert await ns_service.generate_scenario("nope", NeuroshillingGenerateRequest()) is None


@pytest.mark.asyncio
async def test_a_saved_scenario_reads_back_as_a_draft() -> None:
    campaign = await _campaign()

    saved = await ns_service.set_scenario(campaign.campaign_id, _dialogue())
    read = await ns_service.load_scenario(campaign.campaign_id)

    assert saved == read
    assert saved is not None
    assert saved.scenario_status == "draft"
    assert [step.position for step in saved.steps] == [1, 2]


@pytest.mark.asyncio
async def test_editing_steps_resets_approval() -> None:
    campaign = await _approved()

    edited = await ns_service.set_scenario(campaign.campaign_id, _dialogue())

    assert edited is not None
    assert edited.scenario_status == "draft"


@pytest.mark.asyncio
async def test_changing_the_pause_does_not_reset_approval() -> None:
    campaign = await _approved()

    updated = await ns_service.update_campaign(
        campaign.campaign_id,
        NeuroshillingCampaignUpdate(
            name=campaign.name,
            topic=campaign.topic,
            pause_min_seconds=45,
            pause_max_seconds=90,
            messages_per_hour=3,
        ),
    )

    assert updated is not None
    assert updated.scenario_status == "approved"


@pytest.mark.parametrize(
    "edit",
    [
        {"topic": "something else"},
        {"mode": "revive"},
        {"unique_messages": False},
        # The switch that decides whether text written by strangers reaches the
        # model. It sits next to ``unique_messages`` on the card and ships in the
        # same PUT, so an approval left standing over it is an approval given to a
        # dialogue nobody reviewed.
        {"use_chat_context": True},
        # The link and the step it rides on move INDEPENDENTLY: the approval gate
        # checks both, so a case that changes them together cannot tell whether
        # either one alone resets the approval.
        {"media_message_link": "https://t.me/chan/7"},
        {"media_step_position": 1},
    ],
)
@pytest.mark.asyncio
async def test_changing_what_gets_said_resets_approval(edit: dict[str, Any]) -> None:
    campaign = await _approved()
    form: dict[str, Any] = {"name": campaign.name, "topic": campaign.topic, **edit}

    updated = await ns_service.update_campaign(
        campaign.campaign_id,
        NeuroshillingCampaignUpdate(**form),
    )

    assert updated is not None
    assert updated.scenario_status == "draft"


@pytest.mark.asyncio
async def test_renaming_the_campaign_or_retargeting_it_keeps_the_approval() -> None:
    campaign = await _approved()

    updated = await ns_service.update_campaign(
        campaign.campaign_id,
        NeuroshillingCampaignUpdate(
            name="Renamed",
            topic=campaign.topic,
            targets_raw="@news @sport",
        ),
    )

    assert updated is not None
    assert updated.scenario_status == "approved"


@pytest.mark.asyncio
async def test_a_link_that_does_not_point_backwards_is_refused() -> None:
    campaign = await _campaign()
    forward = NeuroshillingScenarioUpdate(
        roles=[NeuroshillingRoleInput(role_id="a", name="A")],
        steps=[
            NeuroshillingStepInput(role_id="a", text="first", reply_to_position=2),
            NeuroshillingStepInput(role_id="a", text="second"),
        ],
    )

    with pytest.raises(ns_service.NeuroshillingInvalidError) as refusal:
        await ns_service.set_scenario(campaign.campaign_id, forward)

    assert refusal.value.code == "scenario_invalid"


@pytest.mark.asyncio
async def test_a_step_pointing_at_itself_is_refused() -> None:
    campaign = await _campaign()
    itself = NeuroshillingScenarioUpdate(
        roles=[NeuroshillingRoleInput(role_id="a", name="A")],
        steps=[NeuroshillingStepInput(role_id="a", text="only", reply_to_position=1)],
    )

    with pytest.raises(ns_service.NeuroshillingInvalidError):
        await ns_service.set_scenario(campaign.campaign_id, itself)


@pytest.mark.asyncio
async def test_more_steps_than_the_operator_configured_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neuroshilling, "max_steps", 1)
    campaign = await _campaign()

    with pytest.raises(ns_service.NeuroshillingInvalidError):
        await ns_service.set_scenario(campaign.campaign_id, _dialogue())


@pytest.mark.asyncio
async def test_more_roles_than_the_operator_configured_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neuroshilling, "max_roles", 0)
    campaign = await _campaign()

    with pytest.raises(ns_service.NeuroshillingInvalidError):
        await ns_service.set_scenario(campaign.campaign_id, _dialogue())


@pytest.mark.parametrize("status", ["running", "stopping"])
@pytest.mark.asyncio
async def test_a_live_run_refuses_every_scenario_write(status: str) -> None:
    campaign = await _campaign(topic="delivery")
    await _set_status(campaign.campaign_id, status)

    for call in (
        ns_service.set_scenario(campaign.campaign_id, _dialogue()),
        ns_service.approve_scenario(campaign.campaign_id),
        ns_service.generate_scenario(campaign.campaign_id, NeuroshillingGenerateRequest()),
    ):
        with pytest.raises(ns_service.NeuroshillingConflictError) as refusal:
            await call
        assert refusal.value.code == "campaign_running"


def _role() -> NeuroshillingRole:
    return NeuroshillingRole(role_id="r", name="A", created_at="2026-01-01T00:00:00+00:00")


def _step(position: int, **overrides: Any) -> NeuroshillingStep:
    fields: dict[str, Any] = {
        "step_id": f"s{position}",
        "position": position,
        "kind": "message",
        "role_id": "r",
        "text": "hi",
        **overrides,
    }
    return NeuroshillingStep(**fields)


@pytest.mark.asyncio
async def test_the_approval_gate_names_the_first_thing_wrong() -> None:
    campaign = await _campaign()

    assert scenario._approval_problem(campaign, [], [_step(1)]) == "no_roles"
    assert (
        scenario._approval_problem(campaign, [_role()], [_step(1, kind="reaction")])
        == "no_message_step"
    )
    assert (
        scenario._approval_problem(campaign, [_role()], [_step(1, role_id=None)])
        == "step_without_role"
    )
    assert scenario._approval_problem(campaign, [_role()], [_step(1)]) is None


@pytest.mark.asyncio
async def test_the_media_slot_must_name_a_step_that_exists() -> None:
    linked = await _campaign(media_message_link="https://t.me/chan/7", media_step_position=4)
    orphan = await _campaign(media_step_position=1)

    assert scenario._approval_problem(linked, [_role()], [_step(1)]) == "media_step_missing"
    assert scenario._approval_problem(orphan, [_role()], [_step(1)]) == "media_step_without_link"


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("загляните на https://t.me/shop", "scenario_text_has_link"),
        ("промокод на первый заказ", "scenario_text_forbidden_word"),
    ],
)
@pytest.mark.asyncio
async def test_a_dialogue_the_send_gate_would_refuse_cannot_be_approved(
    text: str,
    code: str,
) -> None:
    """The two gates are the same two rules, asked where the operator can still act.

    ``_dispatch`` runs ``is_acceptable`` over every send, against settings shared with
    warming whose stock forbidden-word list is the vocabulary a shilling dialogue is
    written in. Approved and launched, such a campaign skipped every message step and
    finished ``done`` having sent nothing, with a warning per step as the only trace.
    """
    campaign = await _campaign(topic="delivery")
    await ns_service.set_scenario(
        campaign.campaign_id,
        NeuroshillingScenarioUpdate(
            roles=[NeuroshillingRoleInput(role_id="a", name="Skeptic")],
            steps=[NeuroshillingStepInput(role_id="a", text=text)],
        ),
    )

    with pytest.raises(ns_service.NeuroshillingInvalidError) as refusal:
        await ns_service.approve_scenario(campaign.campaign_id)

    assert refusal.value.code == code
    stored = await repository.fetch_campaign(campaign.campaign_id)
    assert stored is not None
    assert stored.scenario_status == "draft"


@pytest.mark.asyncio
async def test_an_empty_scenario_cannot_be_approved() -> None:
    campaign = await _campaign(topic="delivery")

    with pytest.raises(ns_service.NeuroshillingInvalidError) as refusal:
        await ns_service.approve_scenario(campaign.campaign_id)

    assert refusal.value.code == "scenario_invalid"
    stored = await repository.fetch_campaign(campaign.campaign_id)
    assert stored is not None
    assert stored.scenario_status == "draft"


@pytest.mark.asyncio
async def test_generation_persists_the_dialogue_and_leaves_it_a_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = await _approved()

    async def _fake(*_args: Any, **_kwargs: Any) -> NeuroshillingScenarioUpdate:
        return _dialogue()

    monkeypatch.setattr(_generate, "generate_dialogue", _fake)
    generated = await ns_service.generate_scenario(
        campaign.campaign_id,
        NeuroshillingGenerateRequest(),
    )
    read = await ns_service.load_scenario(campaign.campaign_id)

    assert generated == read
    assert generated is not None
    assert generated.scenario_status == "draft"
    assert [step.text for step in generated.steps] == ["first", "second"]


@pytest.mark.asyncio
async def test_a_provider_that_produced_nothing_is_a_503_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = await _campaign(topic="delivery")

    async def _nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(_generate, "generate_dialogue", _nothing)

    with pytest.raises(ns_service.NeuroshillingUnavailableError) as refusal:
        await ns_service.generate_scenario(campaign.campaign_id, NeuroshillingGenerateRequest())

    assert refusal.value.code == "llm_unavailable"
    # The claim is released even on the failing path: a second attempt must be
    # refused for the SAME reason, not by a generation that is no longer running.
    with pytest.raises(ns_service.NeuroshillingUnavailableError):
        await ns_service.generate_scenario(campaign.campaign_id, NeuroshillingGenerateRequest())


@pytest.mark.asyncio
async def test_a_second_click_while_one_is_in_flight_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = await _campaign(topic="delivery")
    second: list[Exception] = []

    async def _slow(*_args: Any, **_kwargs: Any) -> NeuroshillingScenarioUpdate:
        try:
            await ns_service.generate_scenario(
                campaign.campaign_id,
                NeuroshillingGenerateRequest(),
            )
        except ns_service.NeuroshillingConflictError as exc:
            second.append(exc)
        return _dialogue()

    monkeypatch.setattr(_generate, "generate_dialogue", _slow)
    await ns_service.generate_scenario(campaign.campaign_id, NeuroshillingGenerateRequest())

    assert [getattr(exc, "code", None) for exc in second] == ["generation_in_progress"]


@pytest.mark.asyncio
async def test_the_daily_budget_refuses_before_anything_is_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neuroshilling, "max_llm_calls_per_day", 1)
    _state.record_llm_call()
    campaign = await _campaign(topic="delivery")

    async def _never_called(*_args: Any, **_kwargs: Any) -> None:  # pragma: no cover
        pytest.fail("the budget must be checked before the provider is reached")

    monkeypatch.setattr(_generate, "generate_dialogue", _never_called)

    with pytest.raises(ns_service.NeuroshillingConflictError) as refusal:
        await ns_service.generate_scenario(campaign.campaign_id, NeuroshillingGenerateRequest())

    assert refusal.value.code == "llm_daily_limit_reached"


@pytest.mark.asyncio
async def test_a_campaign_with_no_topic_is_not_worth_a_paid_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = await _campaign(topic="   ")

    async def _never_called(*_args: Any, **_kwargs: Any) -> None:  # pragma: no cover
        pytest.fail("a dialogue about nothing costs as much as one about something")

    monkeypatch.setattr(_generate, "generate_dialogue", _never_called)

    with pytest.raises(ns_service.NeuroshillingInvalidError) as refusal:
        await ns_service.generate_scenario(campaign.campaign_id, NeuroshillingGenerateRequest())

    assert refusal.value.code == "scenario_invalid"


@pytest.mark.asyncio
async def test_an_ask_bigger_than_the_configured_ceiling_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neuroshilling, "max_roles", 2)
    campaign = await _campaign(topic="delivery")

    with pytest.raises(ns_service.NeuroshillingInvalidError):
        await ns_service.generate_scenario(
            campaign.campaign_id,
            NeuroshillingGenerateRequest(persona_count=5),
        )

    monkeypatch.setattr(settings.neuroshilling, "max_steps", 2)
    with pytest.raises(ns_service.NeuroshillingInvalidError):
        await ns_service.generate_scenario(
            campaign.campaign_id,
            NeuroshillingGenerateRequest(persona_count=2, step_count=9),
        )


@pytest.mark.asyncio
async def test_a_campaign_deleted_mid_save_reports_no_campaign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The repository re-checks inside its own transaction; the service must relay that."""
    campaign = await _campaign(topic="delivery")
    # Saved BEFORE the patch, so the approval gate has a valid scenario to pass and
    # the only thing left to fail is the write itself.
    await ns_service.set_scenario(campaign.campaign_id, _dialogue())

    async def _vanished(*_args: Any, **_kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(repository, "replace_scenario", _vanished)
    monkeypatch.setattr(repository, "approve_scenario", _vanished)

    assert await ns_service.set_scenario(campaign.campaign_id, _dialogue()) is None
    assert await ns_service.approve_scenario(campaign.campaign_id) is None


@pytest.mark.asyncio
async def test_a_rostered_account_still_points_at_a_role_after_regeneration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stored ids travel into the generation, or the roster's FK goes null.

    ``neuroshilling_accounts.role_id`` is ``ON DELETE SET NULL``: a generated cast
    carrying keys that match nothing mints new roles, the keyed upsert deletes the
    old ones, and every account the operator cast loses its part.
    """
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await _campaign(topic="delivery")
    await ns_service.set_scenario(campaign.campaign_id, _dialogue())
    stored = await ns_service.load_scenario(campaign.campaign_id)
    assert stored is not None
    role_id = stored.roles[0].role_id
    await ns_service.update_campaign(
        campaign.campaign_id,
        NeuroshillingCampaignUpdate(
            name="Promo",
            topic="delivery",
            accounts=[NeuroshillingAccountAssignment(account_id="acc-1", role_id=role_id)],
        ),
    )

    async def _recast(*_args: Any, role_ids: Any, **_kwargs: Any) -> NeuroshillingScenarioUpdate:
        return NeuroshillingScenarioUpdate(
            roles=[NeuroshillingRoleInput(role_id=key, name="Recast") for key in role_ids],
            steps=[NeuroshillingStepInput(role_id=role_ids[0], text="new line")],
        )

    monkeypatch.setattr(_generate, "generate_dialogue", _recast)
    await ns_service.generate_scenario(campaign.campaign_id, NeuroshillingGenerateRequest())
    roster = await repository.list_campaign_accounts(campaign.campaign_id)
    regenerated = await ns_service.load_scenario(campaign.campaign_id)

    assert regenerated is not None
    assert [role.name for role in regenerated.roles] == ["Recast"]
    assert roster[0].role_id == role_id


@pytest.mark.asyncio
async def test_a_generated_dialogue_past_the_ceiling_is_refused_not_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ASK is bounded; the answer is not, and the PUT would reject what was written."""
    monkeypatch.setattr(settings.neuroshilling, "max_steps", 2)
    campaign = await _campaign(topic="delivery")

    async def _too_long(*_args: Any, **_kwargs: Any) -> NeuroshillingScenarioUpdate:
        return NeuroshillingScenarioUpdate(
            roles=[NeuroshillingRoleInput(role_id="a", name="A")],
            steps=[NeuroshillingStepInput(role_id="a", text=f"line {index}") for index in range(3)],
        )

    monkeypatch.setattr(_generate, "generate_dialogue", _too_long)

    with pytest.raises(ns_service.NeuroshillingInvalidError) as refusal:
        await ns_service.generate_scenario(
            campaign.campaign_id,
            NeuroshillingGenerateRequest(step_count=2),
        )

    assert refusal.value.code == "scenario_invalid"
    assert await repository.load_scenario(campaign.campaign_id) == ([], [])


@pytest.mark.asyncio
async def test_a_generation_that_never_finishes_is_abandoned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The claim is held for the whole call, and every click 409s until it lets go."""
    monkeypatch.setattr(settings.neuroshilling, "llm_deadline_seconds", 0.01)
    campaign = await _campaign(topic="delivery")

    async def _hangs(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(_generate, "generate_dialogue", _hangs)

    with pytest.raises(ns_service.NeuroshillingUnavailableError) as refusal:
        await ns_service.generate_scenario(campaign.campaign_id, NeuroshillingGenerateRequest())

    assert refusal.value.code == "llm_unavailable"
    # Released, so the operator's next click is answered rather than refused.
    assert _state.try_start_generation(campaign.campaign_id) is None
