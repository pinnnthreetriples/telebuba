"""Tests for ``services.neurocomment.campaigns`` — the page→repository service seam."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import insert, select

from core.db import (
    _get_engine,
    configure_database,
    create_account,
    fetch_account,
    insert_challenge,
    list_campaign_readiness,
    upsert_readiness,
)
from core.repositories.neurocomment._tables import (
    _neurocomment_campaign_accounts,
    _neurocomment_campaign_channels,
    _neurocomment_comments,
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
async def test_pin_account_channel_persists_and_returns_board() -> None:
    """Pinning returns the refreshed board carrying the account's pin; clearing resets it."""
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await campaigns.create_campaign(CampaignCreate(name="A", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@news")
    await campaigns.assign_account_to_campaign(campaign.campaign_id, "acc-1")

    board = await campaigns.pin_account_channel(campaign.campaign_id, "acc-1", "@news")
    assert board is not None
    assert {c.account_id: c.pinned_channel for c in board.accounts} == {"acc-1": "@news"}

    cleared = await campaigns.pin_account_channel(campaign.campaign_id, "acc-1", None)
    assert cleared is not None
    assert {c.account_id: c.pinned_channel for c in cleared.accounts} == {"acc-1": None}


@pytest.mark.asyncio
async def test_pin_account_channel_rejects_foreign_channel() -> None:
    """Pinning to a channel outside the campaign raises ``ChannelNotInCampaignError``."""
    from services.neurocomment import ChannelNotInCampaignError  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await campaigns.create_campaign(CampaignCreate(name="A", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@news")
    await campaigns.assign_account_to_campaign(campaign.campaign_id, "acc-1")

    with pytest.raises(ChannelNotInCampaignError):
        await campaigns.pin_account_channel(campaign.campaign_id, "acc-1", "@other")


@pytest.mark.asyncio
async def test_deactivate_channel_clears_pins_to_it() -> None:
    """Deactivating a channel clears any account pinned to it, else the pin strands the account."""
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await campaigns.create_campaign(CampaignCreate(name="A", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@news")
    await campaigns.assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await campaigns.pin_account_channel(campaign.campaign_id, "acc-1", "@news")

    await campaigns.deactivate_channel(campaign.campaign_id, "@news")

    links = (await campaigns.list_campaign_accounts(campaign.campaign_id)).links
    assert {link.account_id: link.channel for link in links} == {"acc-1": None}


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


@pytest.mark.asyncio
async def test_delete_campaign() -> None:
    campaign = await campaigns.create_campaign(CampaignCreate(name="DeleteMe", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@chan")
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    await campaigns.assign_account_to_campaign(campaign.campaign_id, "acc-1")

    # Insert a dummy comment linked to this campaign and account
    with _get_engine().begin() as conn:
        conn.execute(
            insert(_neurocomment_comments).values(
                channel="@chan",
                post_id=123,
                campaign_id=campaign.campaign_id,
                account_id="acc-1",
                status="posted",
                created_at="2026-06-25T10:00:00Z",
                updated_at="2026-06-25T10:00:00Z",
            ),
        )

    # Verify everything exists before deletion
    assert len((await campaigns.list_campaigns()).campaigns) == 1
    assert len((await campaigns.list_campaign_channels(campaign.campaign_id)).links) == 1
    assert len((await campaigns.list_campaign_accounts(campaign.campaign_id)).links) == 1
    with _get_engine().connect() as conn:
        assert (
            conn.execute(
                select(_neurocomment_comments).where(
                    _neurocomment_comments.c.campaign_id == campaign.campaign_id,
                ),
            ).first()
            is not None
        )

    # Perform the deletion
    await campaigns.delete_campaign(campaign.campaign_id)

    # Verify all records for the campaign are removed from all related tables
    assert len((await campaigns.list_campaigns()).campaigns) == 0
    with _get_engine().connect() as conn:
        # Channels link check
        assert (
            conn.execute(
                select(_neurocomment_campaign_channels).where(
                    _neurocomment_campaign_channels.c.campaign_id == campaign.campaign_id,
                ),
            ).first()
            is None
        )
        # Accounts link check
        assert (
            conn.execute(
                select(_neurocomment_campaign_accounts).where(
                    _neurocomment_campaign_accounts.c.campaign_id == campaign.campaign_id,
                ),
            ).first()
            is None
        )
        # Comments check
        assert (
            conn.execute(
                select(_neurocomment_comments).where(
                    _neurocomment_comments.c.campaign_id == campaign.campaign_id,
                ),
            ).first()
            is None
        )
