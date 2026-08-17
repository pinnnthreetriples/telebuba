"""The scenario repository: one transactional write for roles and steps.

Driven against a real temporary SQLite file rather than a mocked session — the
properties under test are transactional (foreign keys, the position index, the
approval reset riding along with the rows) and none of them exists in a mock.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import insert, select

from core.db import _get_engine, _now_iso, create_account
from core.repositories import neuroshilling as repository
from core.repositories.neuroshilling._tables import (
    _neuroshilling_accounts,
    _neuroshilling_messages,
)
from schemas.accounts import AccountCreate
from schemas.neuroshilling import (
    NeuroshillingAccountAssignment,
    NeuroshillingCampaignCreate,
    NeuroshillingCampaignUpdate,
)
from schemas.neuroshilling_scenario import NeuroshillingRoleInput, NeuroshillingStepInput

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingCampaign


async def _campaign(name: str = "Promo") -> NeuroshillingCampaign:
    return await repository.create_campaign(NeuroshillingCampaignCreate(name=name))


def _role(name: str, key: str | None = None) -> NeuroshillingRoleInput:
    return NeuroshillingRoleInput(role_id=key, name=name, description=f"{name} persona")


def _step(role_key: str | None, text: str, **overrides: Any) -> NeuroshillingStepInput:
    return NeuroshillingStepInput(role_id=role_key, text=text, **overrides)


async def _approve(campaign_id: str) -> bool:
    """Approve at whatever stamp the row carries now — the caller's own read of it."""
    stored = await repository.fetch_campaign(campaign_id)
    assert stored is not None
    return await repository.approve_scenario(
        campaign_id,
        expected_updated_at=stored.updated_at,
    )


@pytest.mark.asyncio
async def test_roles_and_steps_round_trip_in_order() -> None:
    campaign = await _campaign()

    written = await repository.replace_scenario(
        campaign.campaign_id,
        [_role("Skeptic", "a"), _role("Regular", "b")],
        [_step("a", "first"), _step("b", "second", reply_to_position=1)],
    )
    roles, steps = await repository.load_scenario(campaign.campaign_id)

    assert written is True
    assert [role.name for role in roles] == ["Skeptic", "Regular"]
    assert [step.position for step in steps] == [1, 2]
    assert [step.text for step in steps] == ["first", "second"]
    assert steps[1].reply_to_position == 1
    # The client's keys were replaced by minted ids, and the steps follow them.
    assert steps[0].role_id == roles[0].role_id
    assert steps[1].role_id == roles[1].role_id


@pytest.mark.asyncio
async def test_an_unknown_campaign_writes_nothing() -> None:
    written = await repository.replace_scenario("nope", [_role("A", "a")], [])

    assert written is False
    assert await repository.load_scenario("nope") == ([], [])


@pytest.mark.asyncio
async def test_a_step_naming_no_known_role_stores_null_rather_than_failing() -> None:
    """``role_id`` is a real foreign key, so an unresolvable key must not reach it."""
    campaign = await _campaign()

    await repository.replace_scenario(campaign.campaign_id, [], [_step("ghost", "orphan")])
    _roles, steps = await repository.load_scenario(campaign.campaign_id)

    assert steps[0].role_id is None


@pytest.mark.asyncio
async def test_reusing_a_stored_role_id_keeps_the_roster_pointing_at_it() -> None:
    """The whole reason the replace is keyed rather than a truncate."""
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await _campaign()
    await repository.replace_scenario(campaign.campaign_id, [_role("Skeptic", "a")], [])
    roles, _steps = await repository.load_scenario(campaign.campaign_id)
    stored_id = roles[0].role_id
    await repository.update_campaign(
        campaign.campaign_id,
        NeuroshillingCampaignUpdate(
            name="Promo",
            accounts=[NeuroshillingAccountAssignment(account_id="acc-1", role_id=stored_id)],
        ),
    )

    await repository.replace_scenario(
        campaign.campaign_id,
        [NeuroshillingRoleInput(role_id=stored_id, name="Renamed")],
        [],
    )
    roster = await repository.list_campaign_accounts(campaign.campaign_id)
    roles, _steps = await repository.load_scenario(campaign.campaign_id)

    assert [role.role_id for role in roles] == [stored_id]
    assert roles[0].name == "Renamed"
    assert roster[0].role_id == stored_id


@pytest.mark.asyncio
async def test_dropping_a_role_releases_the_roster_entry_that_named_it() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await _campaign()
    await repository.replace_scenario(campaign.campaign_id, [_role("Skeptic", "a")], [])
    roles, _steps = await repository.load_scenario(campaign.campaign_id)
    await repository.update_campaign(
        campaign.campaign_id,
        NeuroshillingCampaignUpdate(
            name="Promo",
            accounts=[
                NeuroshillingAccountAssignment(account_id="acc-1", role_id=roles[0].role_id),
            ],
        ),
    )

    await repository.replace_scenario(campaign.campaign_id, [], [])
    roster = await repository.list_campaign_accounts(campaign.campaign_id)

    assert await repository.load_scenario(campaign.campaign_id) == ([], [])
    assert roster[0].role_id is None


@pytest.mark.asyncio
async def test_two_keyless_roles_do_not_collide() -> None:
    campaign = await _campaign()

    await repository.replace_scenario(
        campaign.campaign_id,
        [_role("First"), _role("Second")],
        [],
    )
    roles, _steps = await repository.load_scenario(campaign.campaign_id)

    assert sorted(role.name for role in roles) == ["First", "Second"]
    assert len({role.role_id for role in roles}) == 2


@pytest.mark.asyncio
async def test_a_shorter_scenario_drops_the_tail_and_its_journal_rows() -> None:
    """SQLite runs with foreign keys ON, so the journal must go with the step."""
    campaign = await _campaign()
    await repository.replace_scenario(
        campaign.campaign_id,
        [_role("A", "a")],
        [_step("a", "one"), _step("a", "two")],
    )
    _roles, steps = await repository.load_scenario(campaign.campaign_id)
    await _journal_row(campaign.campaign_id, steps[1].step_id)

    await repository.replace_scenario(campaign.campaign_id, [_role("A", "a")], [_step("a", "one")])
    _roles, remaining = await repository.load_scenario(campaign.campaign_id)

    assert [step.position for step in remaining] == [1]
    assert await _journal_count() == 0


@pytest.mark.asyncio
async def test_a_step_kept_at_its_position_keeps_its_id() -> None:
    """What lets a journal row from an earlier run still name the step it played."""
    campaign = await _campaign()
    await repository.replace_scenario(campaign.campaign_id, [_role("A", "a")], [_step("a", "one")])
    _roles, before = await repository.load_scenario(campaign.campaign_id)

    await repository.replace_scenario(
        campaign.campaign_id,
        [_role("A", "a")],
        [_step("a", "rewritten"), _step("a", "added")],
    )
    _roles, after = await repository.load_scenario(campaign.campaign_id)

    assert after[0].step_id == before[0].step_id
    assert after[0].text == "rewritten"
    assert after[1].step_id != before[0].step_id


@pytest.mark.asyncio
async def test_writing_the_scenario_returns_an_approved_campaign_to_draft() -> None:
    campaign = await _campaign()
    await repository.replace_scenario(campaign.campaign_id, [_role("A", "a")], [_step("a", "hi")])
    await _approve(campaign.campaign_id)

    await repository.replace_scenario(campaign.campaign_id, [_role("A", "a")], [_step("a", "hi")])
    stored = await repository.fetch_campaign(campaign.campaign_id)

    assert stored is not None
    assert stored.scenario_status == "draft"


@pytest.mark.asyncio
@pytest.mark.parametrize(("clear", "expected"), [(False, 2), (True, None)])
async def test_the_media_slot_is_cleared_only_when_the_write_asks_for_it(
    *,
    clear: bool,
    expected: int | None,
) -> None:
    """Off by default, because a hand-edited scenario keeps the slot the operator chose.

    Both saves and generations reach this one function, and only the generation —
    which replaces every line with text nobody has read — turns the flag on.
    """
    campaign = await _campaign()
    await repository.update_campaign(
        campaign.campaign_id,
        NeuroshillingCampaignUpdate(
            name="Promo",
            media_message_link="https://t.me/chan/7",
            media_step_position=2,
        ),
    )

    await repository.replace_scenario(
        campaign.campaign_id,
        [_role("A", "a")],
        [_step("a", "one"), _step("a", "two")],
        clear_media_step=clear,
    )
    stored = await repository.fetch_campaign(campaign.campaign_id)

    assert stored is not None
    assert stored.media_step_position == expected
    # The link is left where it is either way: it names a message in some other chat
    # and has nothing to do with which step of THIS dialogue carries it.
    assert stored.media_message_link == "https://t.me/chan/7"


@pytest.mark.asyncio
async def test_approve_is_the_only_writer_of_approved() -> None:
    campaign = await _campaign()

    approved = await _approve(campaign.campaign_id)
    stored = await repository.fetch_campaign(campaign.campaign_id)

    assert approved is True
    assert stored is not None
    assert stored.scenario_status == "approved"
    assert await repository.approve_scenario("nope", expected_updated_at=_now_iso()) is False


@pytest.mark.asyncio
async def test_an_approval_naming_a_stamp_the_row_has_left_writes_nothing() -> None:
    """The condition the service's gate rests on: a moved row refuses the approval.

    Every write that changes what the gate validated moves ``updated_at`` in the same
    transaction, so naming the stamp the verdict was reached on is what keeps ``approved``
    off a scenario nobody approved.
    """
    campaign = await _campaign()
    stale = campaign.updated_at
    await repository.replace_scenario(campaign.campaign_id, [_role("A", "a")], [_step("a", "hi")])

    approved = await repository.approve_scenario(
        campaign.campaign_id,
        expected_updated_at=stale,
    )
    stored = await repository.fetch_campaign(campaign.campaign_id)

    assert approved is False
    assert stored is not None
    assert stored.scenario_status == "draft"


@pytest.mark.asyncio
async def test_a_campaign_edit_can_ask_for_the_approval_to_be_dropped() -> None:
    campaign = await _campaign()
    await _approve(campaign.campaign_id)

    kept = await repository.update_campaign(
        campaign.campaign_id,
        NeuroshillingCampaignUpdate(name="Promo"),
    )
    dropped = await repository.update_campaign(
        campaign.campaign_id,
        NeuroshillingCampaignUpdate(name="Promo", topic="new"),
        reset_approval=True,
    )

    assert kept is not None
    assert kept.scenario_status == "approved"
    assert dropped is not None
    assert dropped.scenario_status == "draft"


@pytest.mark.asyncio
async def test_deleting_the_campaign_takes_the_scenario_with_it() -> None:
    campaign = await _campaign()
    await repository.replace_scenario(campaign.campaign_id, [_role("A", "a")], [_step("a", "hi")])

    await repository.delete_campaign(campaign.campaign_id)

    assert await repository.load_scenario(campaign.campaign_id) == ([], [])


async def _journal_row(campaign_id: str, step_id: str) -> None:
    def _write() -> None:
        with _get_engine().begin() as connection:
            connection.execute(
                insert(_neuroshilling_messages).values(
                    campaign_id=campaign_id,
                    run_id="run-1",
                    target="chat",
                    step_id=step_id,
                    account_id="acc-1",
                    status="sent",
                    created_at=_now_iso(),
                ),
            )

    await asyncio.to_thread(_write)


async def _journal_count() -> int:
    def _read() -> int:
        with _get_engine().connect() as connection:
            return len(connection.execute(select(_neuroshilling_messages.c.id)).all())

    return await asyncio.to_thread(_read)


@pytest.mark.asyncio
async def test_the_roster_survives_a_scenario_write_that_touches_no_role() -> None:
    """Guard for the delete-then-insert temptation: the roster is a separate table."""
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await _campaign()
    await repository.update_campaign(
        campaign.campaign_id,
        NeuroshillingCampaignUpdate(
            name="Promo",
            accounts=[NeuroshillingAccountAssignment(account_id="acc-1")],
        ),
    )

    await repository.replace_scenario(campaign.campaign_id, [_role("A", "a")], [_step("a", "hi")])

    def _read() -> int:
        with _get_engine().connect() as connection:
            statement = select(_neuroshilling_accounts.c.account_id).where(
                _neuroshilling_accounts.c.campaign_id == campaign.campaign_id,
            )
            return len(connection.execute(statement).all())

    assert await asyncio.to_thread(_read) == 1
