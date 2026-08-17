"""Neuroshilling campaign policy: what a live run refuses, and what the board says."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import insert
from sqlalchemy import update as sql_update

from core.config import settings
from core.db import _get_engine, _now_iso, create_account, upsert_warming_state
from core.repositories.neuroshilling import (
    claim_chat_reply,
    record_chat_messages,
    record_chat_reply,
    record_presence,
)
from core.repositories.neuroshilling import create_campaign as repo_create_campaign
from core.repositories.neuroshilling._tables import (
    _neuroshilling_campaigns,
    _neuroshilling_roles,
)
from schemas.accounts import AccountCreate
from schemas.neuroshilling import (
    NeuroshillingAccountAssignment,
    NeuroshillingCampaignCreate,
    NeuroshillingCampaignUpdate,
    NeuroshillingChatMessage,
)
from schemas.warming import WarmingStateWrite
from services import _account_owner
from services.neuroshilling import campaigns

if TYPE_CHECKING:
    from schemas.accounts import AccountList
    from schemas.neuroshilling import NeuroshillingBoard, NeuroshillingBoardAccount


async def _account(account_id: str, label: str | None = None) -> None:
    await create_account(
        AccountCreate(account_id=account_id, label=label, session_name=account_id),
    )


async def _role(campaign_id: str, role_id: str) -> None:
    """Insert a role directly — stage one has no roles endpoint to go through."""

    def _run() -> None:
        with _get_engine().begin() as connection:
            connection.execute(
                insert(_neuroshilling_roles).values(
                    role_id=role_id,
                    campaign_id=campaign_id,
                    name=role_id,
                    created_at=_now_iso(),
                ),
            )

    await asyncio.to_thread(_run)


def _rostered(board: NeuroshillingBoard) -> list[NeuroshillingBoardAccount]:
    """The board's roster: one list filtered by the flag, never a second list."""
    return [item for item in board.available if item.assigned]


def _update(**overrides: Any) -> NeuroshillingCampaignUpdate:
    payload: dict[str, Any] = {"name": "Promo", **overrides}
    return NeuroshillingCampaignUpdate(**payload)


async def _set_status(campaign_id: str, status: str) -> None:
    def _run() -> None:
        with _get_engine().begin() as connection:
            connection.execute(
                sql_update(_neuroshilling_campaigns)
                .where(_neuroshilling_campaigns.c.campaign_id == campaign_id)
                .values(status=status),
            )

    await asyncio.to_thread(_run)


@pytest.mark.asyncio
async def test_create_and_list_campaigns() -> None:
    created = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Promo"))

    listed = await campaigns.list_campaigns()

    assert [item.campaign_id for item in listed.campaigns] == [created.campaign_id]


@pytest.mark.asyncio
async def test_targets_are_parsed_the_way_the_paste_box_is_pasted() -> None:
    """One parser for target blobs, the one that already handles invite links."""
    parsed = campaigns.parse_targets(
        "@news, https://t.me/sport\n t.me/+AbCdEfGhIj  @news  rubbish/…",
    )

    assert parsed == ["news", "sport", "+AbCdEfGhIj"]


@pytest.mark.asyncio
async def test_update_persists_the_form_and_its_roster() -> None:
    await _account("acc-1")
    campaign = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Promo"))

    updated = await campaigns.update_campaign(
        campaign.campaign_id,
        _update(
            topic="delivery",
            targets_raw="@news @sport",
            accounts=[NeuroshillingAccountAssignment(account_id="acc-1")],
        ),
    )

    assert updated is not None
    assert updated.topic == "delivery"
    board = await campaigns.load_board(campaign.campaign_id)
    assert board is not None
    assert [item.account_id for item in _rostered(board)] == ["acc-1"]
    assert board.targets == ["news", "sport"]


@pytest.mark.asyncio
async def test_an_account_that_no_longer_exists_is_dropped_from_the_roster() -> None:
    """The picker only offers real accounts, so a ghost id means a lost race.

    Dropping it beats letting the foreign key turn a save into a 500.
    """
    await _account("acc-1")
    campaign = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Promo"))

    updated = await campaigns.update_campaign(
        campaign.campaign_id,
        _update(
            accounts=[
                NeuroshillingAccountAssignment(account_id="acc-1"),
                NeuroshillingAccountAssignment(account_id="ghost"),
            ],
        ),
    )

    assert updated is not None
    board = await campaigns.load_board(campaign.campaign_id)
    assert board is not None
    assert [item.account_id for item in _rostered(board)] == ["acc-1"]


@pytest.mark.asyncio
async def test_the_account_pool_is_read_once_however_long_the_roster_is() -> None:
    """The lookup is hoisted out of the filter, not re-run per roster entry."""
    for index in range(20):
        await _account(f"acc-{index:02d}")
    campaign = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    original = campaigns.list_accounts
    calls = 0

    async def _counted() -> AccountList:
        nonlocal calls
        calls += 1
        return await original()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(campaigns, "list_accounts", _counted)
        await campaigns.update_campaign(
            campaign.campaign_id,
            _update(
                accounts=[
                    NeuroshillingAccountAssignment(account_id=f"acc-{index:02d}")
                    for index in range(20)
                ],
            ),
        )

    assert calls == 1


@pytest.mark.asyncio
async def test_a_role_the_campaign_does_not_own_is_refused() -> None:
    """``role_id`` is a real foreign key, so an unknown one would be a 500."""
    await _account("acc-1")
    campaign = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    other = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Other"))
    await _role(other.campaign_id, "role-elsewhere")

    with pytest.raises(campaigns.NeuroshillingInvalidError) as refusal:
        await campaigns.update_campaign(
            campaign.campaign_id,
            _update(
                accounts=[
                    NeuroshillingAccountAssignment(
                        account_id="acc-1",
                        role_id="role-elsewhere",
                    ),
                ],
            ),
        )

    assert refusal.value.code == "unknown_role"


@pytest.mark.asyncio
async def test_the_campaigns_own_role_is_accepted() -> None:
    await _account("acc-1")
    campaign = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    await _role(campaign.campaign_id, "role-1")

    await campaigns.update_campaign(
        campaign.campaign_id,
        _update(
            accounts=[NeuroshillingAccountAssignment(account_id="acc-1", role_id="role-1")],
        ),
    )

    board = await campaigns.load_board(campaign.campaign_id)
    assert board is not None
    assert [item.role_id for item in _rostered(board)] == ["role-1"]


@pytest.mark.asyncio
async def test_parallel_run_mode_is_refused_by_the_server() -> None:
    """Hiding the option in the UI is not enough — the generated client types it."""
    campaign = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Promo"))

    with pytest.raises(campaigns.NeuroshillingInvalidError) as refusal:
        await campaigns.update_campaign(campaign.campaign_id, _update(run_mode="parallel"))

    assert refusal.value.code == "run_mode_not_supported"


@pytest.mark.asyncio
async def test_too_many_targets_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.neuroshilling, "max_targets_per_campaign", 2)
    campaign = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Promo"))

    with pytest.raises(campaigns.NeuroshillingInvalidError) as refusal:
        await campaigns.update_campaign(
            campaign.campaign_id,
            _update(targets_raw="@one @two @three"),
        )

    assert refusal.value.code == "too_many_targets"
    # Counted on the NORMALISED list: junk and duplicates are not targets.
    assert (
        await campaigns.update_campaign(
            campaign.campaign_id,
            _update(targets_raw="@one @one rubbish/… @two"),
        )
        is not None
    )


@pytest.mark.parametrize("status", ["running", "stopping"])
@pytest.mark.asyncio
async def test_a_live_campaign_refuses_edits_and_deletion(status: str) -> None:
    campaign = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    await _set_status(campaign.campaign_id, status)

    with pytest.raises(campaigns.NeuroshillingConflictError) as edit:
        await campaigns.update_campaign(campaign.campaign_id, _update())
    with pytest.raises(campaigns.NeuroshillingConflictError) as removal:
        await campaigns.delete_campaign(campaign.campaign_id)

    assert edit.value.code == "campaign_running"
    assert removal.value.code == "campaign_running"


@pytest.mark.asyncio
async def test_unknown_campaigns_report_absence_rather_than_refusal() -> None:
    assert await campaigns.update_campaign("nope", _update()) is None
    assert await campaigns.delete_campaign("nope") is False
    assert await campaigns.load_board("nope") is None


@pytest.mark.asyncio
async def test_delete_removes_an_idle_campaign() -> None:
    campaign = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Promo"))

    assert await campaigns.delete_campaign(campaign.campaign_id) is True
    assert (await campaigns.list_campaigns()).campaigns == []


@pytest.mark.asyncio
async def test_the_board_carries_the_whole_account_pool_and_the_run_state() -> None:
    await _account("acc-1", "Alice")
    await _account("acc-2")
    campaign = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    await campaigns.update_campaign(
        campaign.campaign_id,
        _update(accounts=[NeuroshillingAccountAssignment(account_id="acc-1", is_reserve=True)]),
    )

    board = await campaigns.load_board(campaign.campaign_id)

    assert board is not None
    assert {item.account_id for item in board.available} == {"acc-1", "acc-2"}
    # A label is the operator's name for an account; an unlabelled one falls back
    # to its id rather than rendering as an empty row.
    assert {item.account_id: item.title for item in board.available} == {
        "acc-1": "Alice",
        "acc-2": "acc-2",
    }
    assert [(item.account_id, item.is_reserve, item.state) for item in _rostered(board)] == [
        ("acc-1", True, "active"),
    ]
    # The run state is the campaign row's — there is no second copy of it.
    assert (board.campaign.status, board.campaign.run_id) == ("idle", None)


@pytest.mark.asyncio
async def test_the_board_marks_an_account_warming_holds() -> None:
    await _account("acc-1")
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))
    campaign = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Promo"))

    board = await campaigns.load_board(campaign.campaign_id)

    assert board is not None
    assert [(item.busy_owner, item.busy_campaign_name) for item in board.available] == [
        ("warming", None),
    ]


@pytest.mark.asyncio
async def test_the_board_marks_an_account_another_running_campaign_holds() -> None:
    """A campaign still marked running holds its roster even after a restart."""
    await _account("acc-1")
    other = await repo_create_campaign(NeuroshillingCampaignCreate(name="Other"))
    await campaigns.update_campaign(
        other.campaign_id,
        _update(name="Other", accounts=[NeuroshillingAccountAssignment(account_id="acc-1")]),
    )
    await _set_status(other.campaign_id, "running")
    mine = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Mine"))

    board = await campaigns.load_board(mine.campaign_id)

    assert board is not None
    assert [(item.busy_owner, item.busy_campaign_name) for item in board.available] == [
        ("neuroshilling", "Other"),
    ]


@pytest.mark.asyncio
async def test_a_campaign_does_not_report_its_own_running_roster_as_busy() -> None:
    """Busy in the picker means held ELSEWHERE — otherwise every row greys itself out."""
    await _account("acc-1")
    campaign = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    await campaigns.update_campaign(
        campaign.campaign_id,
        _update(accounts=[NeuroshillingAccountAssignment(account_id="acc-1")]),
    )
    await _set_status(campaign.campaign_id, "running")
    _account_owner.try_claim("acc-1", "neuroshilling", campaign.campaign_id)

    board = await campaigns.load_board(campaign.campaign_id)

    assert board is not None
    assert [item.busy_owner for item in board.available] == [None]


@pytest.mark.asyncio
async def test_a_self_held_account_keeps_a_hold_that_is_not_ours() -> None:
    """Clearing our own marker must not clear somebody else's on the same account."""
    await _account("acc-1")
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))
    campaign = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    _account_owner.try_claim("acc-1", "neuroshilling", campaign.campaign_id)

    board = await campaigns.load_board(campaign.campaign_id)

    assert board is not None
    assert [item.busy_owner for item in board.available] == ["warming"]


@pytest.mark.asyncio
async def test_a_warming_hold_is_never_labelled_with_a_campaign_name() -> None:
    """The name belongs to whoever HOLDS the account, not to any running roster."""
    await _account("acc-1")
    other = await repo_create_campaign(NeuroshillingCampaignCreate(name="Other"))
    await campaigns.update_campaign(
        other.campaign_id,
        _update(name="Other", accounts=[NeuroshillingAccountAssignment(account_id="acc-1")]),
    )
    await _set_status(other.campaign_id, "running")
    mine = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Mine"))
    _account_owner.try_claim("acc-1", "warming", "run-1")

    board = await campaigns.load_board(mine.campaign_id)

    assert board is not None
    assert [(item.busy_owner, item.busy_campaign_name) for item in board.available] == [
        ("warming", None),
    ]


@pytest.mark.asyncio
async def test_the_registry_overrules_the_durable_rows() -> None:
    """Once a run is live the registry is the truth; the DB rows only back it up."""
    await _account("acc-1")
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))
    campaign = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    _account_owner.try_claim("acc-1", "neuroshilling", "some-other-campaign")

    board = await campaigns.load_board(campaign.campaign_id)

    assert board is not None
    assert [item.busy_owner for item in board.available] == ["neuroshilling"]


@pytest.mark.asyncio
async def test_the_launch_card_reports_a_halt_another_campaign_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The card and the join gate must answer out of the same rows and the same window.

    A flood is a verdict on the ACCOUNT and binds every campaign that account is on,
    so a card reading only its own presence rows stayed quiet about exactly the
    accounts its next run would refuse to play. An expired flood binds neither.
    """
    await _account("acc-1")
    elsewhere = await repo_create_campaign(NeuroshillingCampaignCreate(name="Other"))
    mine = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Mine"))
    await campaigns.update_campaign(
        mine.campaign_id,
        _update(name="Mine", accounts=[NeuroshillingAccountAssignment(account_id="acc-1")]),
    )
    await record_presence(elsewhere.campaign_id, "acc-1", "@a", "flooded")

    status = await campaigns.run_status(mine.campaign_id)

    assert status is not None
    assert status.halted_accounts == ["acc-1"]
    # A cooldown of zero puts the cutoff at "now", which every stored row predates.
    monkeypatch.setattr(settings.neuroshilling, "flood_cooldown_seconds", 0.0)
    expired = await campaigns.run_status(mine.campaign_id)
    assert expired is not None
    assert expired.halted_accounts == []


@pytest.mark.parametrize(
    ("switches", "status", "expected"),
    [
        ({"use_chat_context": True}, "running", True),
        ({"reply_to_humans": True}, "running", True),
        ({"autoresponder": "neurodialog"}, "running", True),
        ({}, "running", False),
        # The switches are on the campaign row already; what the card cannot read
        # off them is whether a run is in flight to act on them.
        ({"use_chat_context": True}, "idle", False),
    ],
)
@pytest.mark.asyncio
async def test_listening_needs_both_a_switch_and_a_live_run(
    switches: dict[str, Any],
    status: str,
    *,
    expected: bool,
) -> None:
    created = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Mine"))
    await campaigns.update_campaign(created.campaign_id, _update(name="Mine", **switches))
    await _set_status(created.campaign_id, status)

    run = await campaigns.run_status(created.campaign_id)

    assert run is not None
    assert run.listening is expected


@pytest.mark.asyncio
async def test_the_listener_counters_come_from_the_chat_log() -> None:
    """``seen`` counts every observed row; ``replied`` counts published answers only."""
    created = await campaigns.create_campaign(NeuroshillingCampaignCreate(name="Mine"))
    await record_chat_messages(
        created.campaign_id,
        "@a",
        [
            NeuroshillingChatMessage(message_id=7, text="hi"),
            NeuroshillingChatMessage(message_id=8, text="hey"),
        ],
    )
    await claim_chat_reply(created.campaign_id, "@a", 7)
    await record_chat_reply(created.campaign_id, "@a", 7, account_id="acc-1")

    run = await campaigns.run_status(created.campaign_id)

    assert run is not None
    assert (run.chat_messages_seen, run.human_replies_sent) == (2, 1)
