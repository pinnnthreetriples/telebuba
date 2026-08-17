"""Presence rows: membership recorded per (account, target), not per target."""

from __future__ import annotations

import pytest

from core.db import create_account
from core.repositories.neuroshilling import (
    create_campaign,
    delete_campaign,
    fetch_presence_state,
    list_halted_accounts,
    list_presence,
    record_presence,
    retire_account_presence,
    update_campaign,
)
from schemas.accounts import AccountCreate
from schemas.neuroshilling import (
    NeuroshillingAccountAssignment,
    NeuroshillingCampaignCreate,
    NeuroshillingCampaignUpdate,
)

# A cutoff every stored row is newer than: "no flood has expired yet".
_IN_FORCE = "1970-01-01T00:00:00+00:00"
# A cutoff no stored row can reach: "every flood has expired".
_EXPIRED = "9999-01-01T00:00:00+00:00"


async def _campaign() -> str:
    created = await create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    return created.campaign_id


@pytest.mark.asyncio
async def test_two_accounts_hold_independent_state_for_the_same_target() -> None:
    """The whole reason the table is keyed by the pair.

    A chat id lives in an account's own session cache, so "we are in this chat" can
    be true of one account and false of another at the same instant.
    """
    campaign_id = await _campaign()

    await record_presence(campaign_id, "acc-1", "+HASH", "joined")
    await record_presence(campaign_id, "acc-2", "+HASH", "pending_approval")

    rows = await list_presence(campaign_id, target="+HASH")

    assert [(row.account_id, row.state) for row in rows] == [
        ("acc-1", "joined"),
        ("acc-2", "pending_approval"),
    ]


@pytest.mark.asyncio
async def test_a_join_stamps_a_time_and_a_later_refusal_does_not_erase_it() -> None:
    campaign_id = await _campaign()
    await record_presence(campaign_id, "acc-1", "+HASH", "joined")
    joined_at = (await list_presence(campaign_id))[0].joined_at

    await record_presence(campaign_id, "acc-1", "+HASH", "flooded", error_type="FloodWait")

    row = (await list_presence(campaign_id))[0]
    assert (row.state, row.last_error_type) == ("flooded", "FloodWait")
    assert row.joined_at == joined_at is not None


@pytest.mark.asyncio
async def test_recording_the_same_pair_twice_updates_rather_than_duplicates() -> None:
    campaign_id = await _campaign()

    await record_presence(campaign_id, "acc-1", "@group", "pending")
    await record_presence(campaign_id, "acc-1", "@group", "refused", error_type="InviteHashExpired")

    rows = await list_presence(campaign_id)
    assert len(rows) == 1
    assert (rows[0].state, rows[0].last_error_type) == ("refused", "InviteHashExpired")


@pytest.mark.asyncio
async def test_an_account_wide_verdict_covers_every_target_it_was_still_playing() -> None:
    """A flood belongs to the ACCOUNT, so it applies wherever that account was going.

    Persisted rather than held in a run-local set: a restart forgets memory, and the
    account would start posting again inside the window Telegram is still counting.
    """
    campaign_id = await _campaign()
    await record_presence(campaign_id, "acc-1", "@a", "joined")
    await record_presence(campaign_id, "acc-1", "@b", "pending")
    await record_presence(campaign_id, "acc-1", "@c", "refused", error_type="ChatRestricted")
    await record_presence(campaign_id, "acc-2", "@a", "joined")

    changed = await retire_account_presence("acc-1", "flooded")

    assert changed == 2
    states = {(row.account_id, row.target): row.state for row in await list_presence(campaign_id)}
    assert states == {
        ("acc-1", "@a"): "flooded",
        ("acc-1", "@b"): "flooded",
        # Its own refusal survives: overwriting it would lose the only record of WHY
        # this particular target said no.
        ("acc-1", "@c"): "refused",
        ("acc-2", "@a"): "joined",
    }


@pytest.mark.asyncio
async def test_an_account_wide_verdict_is_not_confined_to_one_campaign() -> None:
    """Nothing makes an account exclusive to a campaign, and Telegram counts per account.

    Scoped to the campaign that hit the flood, the verdict left the same account fully
    live in the other one — working through the identical limit from the other side.
    """
    flooded_campaign = await _campaign()
    other_campaign = await _campaign()
    await record_presence(flooded_campaign, "acc-1", "@a", "joined")
    await record_presence(other_campaign, "acc-1", "@b", "joined")

    assert await retire_account_presence("acc-1", "flooded") == 2

    assert (await list_presence(other_campaign))[0].state == "flooded"


@pytest.mark.asyncio
async def test_a_pairs_stored_state_is_what_answers_the_next_join() -> None:
    campaign_id = await _campaign()
    await record_presence(campaign_id, "acc-1", "@a", "joined")

    assert await fetch_presence_state(campaign_id, "acc-1", "@a", flood_since=_IN_FORCE) == "joined"
    # A pair with no row at all: nothing has been learnt about it yet.
    assert await fetch_presence_state(campaign_id, "acc-1", "@b", flood_since=_IN_FORCE) is None
    # Another account's row is not an answer about this one.
    assert await fetch_presence_state(campaign_id, "acc-2", "@a", flood_since=_IN_FORCE) is None


@pytest.mark.asyncio
async def test_an_account_halt_answers_for_a_target_it_has_no_row_for() -> None:
    """The halt is a verdict on the ACCOUNT, and the next target has no row to carry it.

    Read per pair only, a flooded account would join the very next chat on the list —
    the retirement sweep can only stamp rows that already exist.
    """
    flooded_campaign = await _campaign()
    other_campaign = await _campaign()
    await record_presence(flooded_campaign, "acc-1", "@a", "flooded")

    for campaign_id in (flooded_campaign, other_campaign):
        state = await fetch_presence_state(campaign_id, "acc-1", "@unseen", flood_since=_IN_FORCE)
        assert state == "flooded"


@pytest.mark.asyncio
async def test_a_flood_stops_answering_once_its_window_has_passed() -> None:
    """The verdict has no other way back: the retirement sweep only stamps live pairs.

    Unbounded, a thirty-second wait took the account out of every campaign for good.
    ``retired`` is not on the same clock — the 500-chat ceiling and a dead session do
    not expire — so it goes on answering.
    """
    campaign_id = await _campaign()
    await record_presence(campaign_id, "acc-1", "@a", "flooded")
    await record_presence(campaign_id, "acc-2", "@a", "retired")

    assert await fetch_presence_state(campaign_id, "acc-1", "@a", flood_since=_EXPIRED) is None
    assert await fetch_presence_state(campaign_id, "acc-1", "@b", flood_since=_EXPIRED) is None
    assert await fetch_presence_state(campaign_id, "acc-2", "@b", flood_since=_EXPIRED) == "retired"


@pytest.mark.asyncio
async def test_the_halted_roster_reads_verdicts_written_by_another_campaign() -> None:
    """What the launch card shows must be what the join gate will honour.

    The verdict is about the ACCOUNT and binds whichever campaign recorded it, so a
    card that only read its own presence rows stayed silent about exactly the accounts
    its next run would refuse to play.
    """
    reporting = await _campaign()
    elsewhere = await _campaign()
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    await update_campaign(
        reporting,
        NeuroshillingCampaignUpdate(
            name="Promo",
            accounts=[NeuroshillingAccountAssignment(account_id="acc-1")],
        ),
    )
    await record_presence(elsewhere, "acc-1", "@a", "flooded")

    assert await list_halted_accounts(reporting, flood_since=_IN_FORCE) == ["acc-1"]
    assert await list_halted_accounts(reporting, flood_since=_EXPIRED) == []


@pytest.mark.asyncio
async def test_presence_dies_with_its_campaign() -> None:
    campaign_id = await _campaign()
    await record_presence(campaign_id, "acc-1", "@a", "joined")

    await delete_campaign(campaign_id)

    assert await list_presence(campaign_id) == []


@pytest.mark.asyncio
async def test_listing_without_a_target_returns_the_whole_campaign() -> None:
    campaign_id = await _campaign()
    await record_presence(campaign_id, "acc-1", "@b", "joined")
    await record_presence(campaign_id, "acc-1", "@a", "joined")

    rows = await list_presence(campaign_id)

    assert [row.target for row in rows] == ["@a", "@b"]
