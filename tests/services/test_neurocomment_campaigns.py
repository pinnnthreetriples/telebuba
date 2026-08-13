"""Tests for ``services.neurocomment.campaigns`` — the page→repository service seam."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    bump_channel_pause,
    configure_database,
    create_account,
    fetch_account,
    insert_challenge,
    list_campaign_readiness,
    stamp_rejoin_attempt,
    upsert_readiness,
)
from schemas.accounts import AccountCreate
from schemas.challenge import ChallengeInsert
from schemas.neurocomment import CampaignCreate
from services.accounts import remove_account
from services.neurocomment import campaigns

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_database(tmp_path / "telebuba.db")

    # By default the runtime is stopped, so a link/unlink must not touch the listener.
    # Individual tests override this spy to assert reconcile is (or isn't) called.
    async def _noop() -> None:
        return None

    monkeypatch.setattr(campaigns._runtime, "reconcile_if_running", _noop)


@pytest.mark.asyncio
async def test_create_and_list_campaigns() -> None:
    created = await campaigns.create_campaign(CampaignCreate(name="Promo", prompt="p"))
    assert created.name == "Promo"
    listed = await campaigns.list_campaigns()
    assert [c.campaign_id for c in listed.campaigns] == [created.campaign_id]


@pytest.mark.asyncio
async def test_list_campaigns_carries_per_campaign_channel_and_account_counts() -> None:
    """Every listed campaign carries real channel/account counts (not just the selected one, #1)."""
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    await create_account(AccountCreate(account_id="acc-2", label="B", session_name="acc-2"))

    a = await campaigns.create_campaign(CampaignCreate(name="A", prompt="p"))
    b = await campaigns.create_campaign(CampaignCreate(name="B", prompt="p"))

    # A: two channels, one account. B: one channel, two accounts.
    await campaigns.link_channel(a.campaign_id, "@a1")
    await campaigns.link_channel(a.campaign_id, "@a2")
    await campaigns.assign_account_to_campaign(a.campaign_id, "acc-1")
    await campaigns.link_channel(b.campaign_id, "@b1")
    await campaigns.assign_account_to_campaign(b.campaign_id, "acc-1")
    await campaigns.assign_account_to_campaign(b.campaign_id, "acc-2")

    # A deactivated channel must not be counted.
    await campaigns.deactivate_channel(a.campaign_id, "@a2")

    by_id = {c.campaign_id: c for c in (await campaigns.list_campaigns()).campaigns}

    assert by_id[a.campaign_id].channel_count == 1  # @a2 was deactivated
    assert by_id[a.campaign_id].account_count == 1
    assert by_id[b.campaign_id].channel_count == 1
    assert by_id[b.campaign_id].account_count == 2


@pytest.mark.asyncio
async def test_set_account_channels_persists_and_returns_board() -> None:
    """Setting the subset returns the refreshed board carrying it; clearing resets it."""
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await campaigns.create_campaign(CampaignCreate(name="A", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@news")
    await campaigns.link_channel(campaign.campaign_id, "@sport")
    await campaigns.assign_account_to_campaign(campaign.campaign_id, "acc-1")

    board = await campaigns.set_account_channels(campaign.campaign_id, "acc-1", ["@news", "@sport"])
    assert board is not None
    assert {c.account_id: c.pinned_channels for c in board.accounts} == {
        "acc-1": ["@news", "@sport"],
    }

    cleared = await campaigns.set_account_channels(campaign.campaign_id, "acc-1", [])
    assert cleared is not None
    assert {c.account_id: c.pinned_channels for c in cleared.accounts} == {"acc-1": []}


@pytest.mark.asyncio
async def test_set_account_channels_rejects_foreign_channel() -> None:
    """A channel outside the campaign raises ``ChannelNotInCampaignError``."""
    from services.neurocomment import ChannelNotInCampaignError  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await campaigns.create_campaign(CampaignCreate(name="A", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@news")
    await campaigns.assign_account_to_campaign(campaign.campaign_id, "acc-1")

    with pytest.raises(ChannelNotInCampaignError):
        await campaigns.set_account_channels(campaign.campaign_id, "acc-1", ["@other"])


@pytest.mark.asyncio
async def test_deactivate_channel_clears_pins_to_it() -> None:
    """Deactivating a channel drops it from every account's subset, else it strands them."""
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await campaigns.create_campaign(CampaignCreate(name="A", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@news")
    await campaigns.assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await campaigns.set_account_channels(campaign.campaign_id, "acc-1", ["@news"])

    await campaigns.deactivate_channel(campaign.campaign_id, "@news")

    links = (await campaigns.list_campaign_accounts(campaign.campaign_id)).links
    assert {link.account_id: link.channels for link in links} == {"acc-1": []}


@pytest.mark.asyncio
async def test_link_channel_reconciles_only_when_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Linking a channel re-points a running listener; while stopped it does nothing (#2)."""
    calls: list[str] = []

    async def _reconcile() -> None:
        calls.append("reconcile")

    monkeypatch.setattr(campaigns._runtime, "reconcile_if_running", _reconcile)

    campaign = await campaigns.create_campaign(CampaignCreate(name="A", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@a")
    await campaigns.deactivate_channel(campaign.campaign_id, "@a")

    # Both mutations delegate the running/stopped decision to reconcile_if_running.
    assert calls == ["reconcile", "reconcile"]


@pytest.mark.asyncio
async def test_set_status_persists_and_reconciles(monkeypatch: pytest.MonkeyPatch) -> None:
    """The status route's service persists the change and re-points a running listener (#6)."""
    calls: list[str] = []

    async def _reconcile() -> None:
        calls.append("reconcile")

    monkeypatch.setattr(campaigns._runtime, "reconcile_if_running", _reconcile)

    campaign = await campaigns.create_campaign(CampaignCreate(name="A", prompt="p"))
    assert campaign.status == "active"

    await campaigns.set_status(campaign.campaign_id, "paused")
    listed = {c.campaign_id: c for c in (await campaigns.list_campaigns()).campaigns}
    assert listed[campaign.campaign_id].status == "paused"

    await campaigns.set_status(campaign.campaign_id, "active")
    listed = {c.campaign_id: c for c in (await campaigns.list_campaigns()).campaigns}
    assert listed[campaign.campaign_id].status == "active"

    assert calls == ["reconcile", "reconcile"]


@pytest.mark.asyncio
async def test_link_channel_reports_clash_instead_of_raising() -> None:
    a = await campaigns.create_campaign(CampaignCreate(name="A", prompt="p"))
    b = await campaigns.create_campaign(CampaignCreate(name="B", prompt="p"))

    first = await campaigns.link_channel(a.campaign_id, "@chan")
    assert first.status == "linked"
    assert first.channel == "@chan"
    channels = await campaigns.list_campaign_channels(a.campaign_id)
    assert [link.channel for link in channels.links] == ["@chan"]

    # The channel is the active target of A → linking it to B is reported, not raised.
    clash = await campaigns.link_channel(b.campaign_id, "@chan")
    assert clash.status == "already_assigned"

    # Freeing it from A lets B take it.
    await campaigns.deactivate_channel(a.campaign_id, "@chan")
    moved = await campaigns.link_channel(b.campaign_id, "@chan")
    assert moved.status == "linked"


@pytest.mark.asyncio
async def test_assign_and_remove_account() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await campaigns.create_campaign(CampaignCreate(name="A", prompt="p"))

    await campaigns.assign_account_to_campaign(campaign.campaign_id, "acc-1")
    assigned = await campaigns.list_campaign_accounts(campaign.campaign_id)
    assert [link.account_id for link in assigned.links] == ["acc-1"]

    await campaigns.remove_account_from_campaign(campaign.campaign_id, "acc-1")
    assert (await campaigns.list_campaign_accounts(campaign.campaign_id)).links == []


@pytest.mark.asyncio
async def test_assign_account_reconciles_running_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assigning an account re-points a running listener (which onboards it); removal doesn't.

    The NOXX failure: the account was assigned last, after every channel link, and
    nothing re-triggered onboarding — the campaign sat with zero readiness rows.
    """
    calls: list[str] = []

    async def _reconcile() -> None:
        calls.append("reconcile")

    monkeypatch.setattr(campaigns._runtime, "reconcile_if_running", _reconcile)
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await campaigns.create_campaign(CampaignCreate(name="A", prompt="p"))

    await campaigns.assign_account_to_campaign(campaign.campaign_id, "acc-1")
    assert calls == ["reconcile"]

    await campaigns.remove_account_from_campaign(campaign.campaign_id, "acc-1")
    assert calls == ["reconcile"]  # unassign stays trigger-free (no onboarding needed)


@pytest.mark.asyncio
async def test_list_campaign_challenges_merges_failed_across_channels() -> None:
    campaign = await campaigns.create_campaign(CampaignCreate(name="C", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@a")
    await campaigns.link_channel(campaign.campaign_id, "@b")
    for challenge_hash, account_id, channel, outcome in (
        ("h1", "acc1", "@a", "failed"),
        ("h2", "acc2", "@b", "give_up"),
        ("h3", "acc3", "@a", "solved"),  # solved → never in the queue
    ):
        await insert_challenge(
            ChallengeInsert(
                challenge_hash=challenge_hash,
                account_id=account_id,
                channel=channel,
                raw_text="captcha",
                outcome=outcome,
            ),
        )

    queue = await campaigns.list_campaign_challenges(campaign.campaign_id, 10)

    # Both channels' unsolved rows are merged; the solved one is excluded.
    assert {row.channel for row in queue.rows} == {"@a", "@b"}
    assert {row.outcome for row in queue.rows} <= {"failed", "give_up"}
    assert len(queue.rows) == 2


async def _challenged_pair(campaign_id: str, account_id: str, channel: str) -> None:
    await create_account(
        AccountCreate(account_id=account_id, label=account_id, session_name=account_id)
    )
    await campaigns.assign_account_to_campaign(campaign_id, account_id)
    await insert_challenge(
        ChallengeInsert(
            challenge_hash=f"h-{account_id}",
            account_id=account_id,
            channel=channel,
            raw_text="captcha",
            outcome="give_up",
        ),
    )


@pytest.mark.asyncio
async def test_list_campaign_challenges_hides_pairs_with_no_rejoin_left() -> None:
    """The live report: six rows for one channel, every account long out of its chat.

    Both pairs lost access the same way (``ChannelPrivateError``); only "spent" has used up
    its re-join budget. «Повторить» erases readiness and re-onboards, so for "spent" the
    join RPC is the one Telegram has already refused four times — it burns a slot of the
    account's daily join cap and restores a budget the rule spent four days ending. "trying"
    may still get back in, which is why the budget and not the access loss decides.
    """
    campaign = await campaigns.create_campaign(CampaignCreate(name="C", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@chan")
    for account_id in ("trying", "spent"):
        await _challenged_pair(campaign.campaign_id, account_id, "@chan")
        await upsert_readiness(
            account_id,
            "@chan",
            joined=False,
            captcha_passed=True,
            ready=False,
            access_lost_reason="ChannelPrivateError",
        )
    for _ in range(settings.neurocomment.channel_max_rounds):
        await stamp_rejoin_attempt("spent", "@chan")

    queue = await campaigns.list_campaign_challenges(campaign.campaign_id, 10)

    assert [row.account_id for row in queue.rows] == ["trying"]


@pytest.mark.asyncio
async def test_hidden_rows_do_not_consume_the_limit() -> None:
    """Every exclusion must be inside the statement the database applies ``limit`` to.

    The queue lists challenge ROWS and a pair collects a new one on every pass that meets
    the guardian bot, so a handful of finished pairs is easily more rows than the whole
    limit. Filtered after the query they fill the page and hide the one pair a human can
    still act on — which is the very blindness this view exists to remove.
    """
    campaign = await campaigns.create_campaign(CampaignCreate(name="C", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@chan")
    # Inserted FIRST, so this actionable pair is the oldest row and all 12 hidden rows sort
    # ahead of it: with limit=10 a post-query filter returns an empty queue.
    await _challenged_pair(campaign.campaign_id, "live", "@chan")
    for index in range(3):
        account_id = f"spent-{index}"
        await _challenged_pair(campaign.campaign_id, account_id, "@chan")
        # Four give_up rows for the same pair, as four onboarding passes would leave.
        for _ in range(3):
            await insert_challenge(
                ChallengeInsert(
                    challenge_hash=f"h-{account_id}-again",
                    account_id=account_id,
                    channel="@chan",
                    raw_text="captcha",
                    outcome="give_up",
                ),
            )
        await upsert_readiness(
            account_id,
            "@chan",
            joined=False,
            captcha_passed=True,
            ready=False,
            access_lost_reason="ChannelPrivateError",
        )
        for _ in range(settings.neurocomment.channel_max_rounds):
            await stamp_rejoin_attempt(account_id, "@chan")

    queue = await campaigns.list_campaign_challenges(campaign.campaign_id, 10)

    assert [row.account_id for row in queue.rows] == ["live"]


@pytest.mark.asyncio
async def test_list_campaign_challenges_hides_a_paused_channel() -> None:
    """A #147 pause is the one state where «Повторить» provably does nothing at all.

    ``_join_and_classify`` returns ``channel_paused`` before the join RPC, so no join, no
    solver, no readiness write — the click is a no-op. Hidden for the window only: the rows
    return once it lapses, which is why this is not folded into the age cutoff.
    """
    campaign = await campaigns.create_campaign(CampaignCreate(name="C", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@paused")
    await campaigns.link_channel(campaign.campaign_id, "@live")
    await _challenged_pair(campaign.campaign_id, "acc-paused", "@paused")
    await _challenged_pair(campaign.campaign_id, "acc-live", "@live")

    await bump_channel_pause("@paused", (datetime.now(UTC) + timedelta(hours=5)).isoformat())

    queue = await campaigns.list_campaign_challenges(campaign.campaign_id, 10)

    assert [row.account_id for row in queue.rows] == ["acc-live"]

    # A lapsed deadline is not a pause: the rows come back on their own.
    await bump_channel_pause("@paused", (datetime.now(UTC) - timedelta(hours=1)).isoformat())
    reopened = await campaigns.list_campaign_challenges(campaign.campaign_id, 10)
    assert {row.account_id for row in reopened.rows} == {"acc-live", "acc-paused"}


@pytest.mark.asyncio
async def test_list_campaign_challenges_drops_rows_past_the_age_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window is a setting, and the queue honours it (0 days = nothing qualifies)."""
    campaign = await campaigns.create_campaign(CampaignCreate(name="C", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@chan")
    await _challenged_pair(campaign.campaign_id, "fresh", "@chan")

    assert len((await campaigns.list_campaign_challenges(campaign.campaign_id, 10)).rows) == 1

    monkeypatch.setattr(settings.neurocomment, "challenge_queue_max_age_days", 1e-9)

    assert (await campaigns.list_campaign_challenges(campaign.campaign_id, 10)).rows == []


@pytest.mark.asyncio
async def test_remove_account_clears_neurocomment_links() -> None:
    """Deleting a campaign-assigned account must not explode on the FK (was a 500)."""
    campaign = await campaigns.create_campaign(CampaignCreate(name="C", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@chan")
    await create_account(AccountCreate(account_id="neuro-acc", label="A", session_name="neuro-acc"))
    await campaigns.assign_account_to_campaign(campaign.campaign_id, "neuro-acc")
    await upsert_readiness("neuro-acc", "@chan", joined=True, captcha_passed=True, ready=True)

    # Previously raised IntegrityError (FK accounts) → 500; now the children go first.
    await remove_account("neuro-acc")

    assert await fetch_account("neuro-acc") is None
    assert (await campaigns.list_campaign_accounts(campaign.campaign_id)).links == []
    readiness = (await list_campaign_readiness(campaign.campaign_id)).readiness
    assert all(r.account_id != "neuro-acc" for r in readiness)
