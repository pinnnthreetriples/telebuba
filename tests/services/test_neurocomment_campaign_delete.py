"""What a campaign delete records, and what it tells the listener afterwards.

Split out of ``test_neurocomment_campaigns`` at the 700-line cap. The seam is the subject:
everything here is about the one irreversible action in the domain — the journal line that
is its only record, and the watch-set rebuild that keeps the listener from awaiting posts
from channels the campaign no longer has.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import insert, select

from core.db import (
    _get_engine,
    configure_database,
    create_account,
    list_recent_logs,
    set_listener_account_id,
)
from core.repositories.neurocomment._tables import (
    _neurocomment_campaign_accounts,
    _neurocomment_campaign_channels,
    _neurocomment_comments,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from services.neurocomment import _lifecycle, campaigns

if TYPE_CHECKING:
    from pathlib import Path

    from schemas.logs import LogEntry


# Same isolation the sibling file uses: its own DB per test, and a stopped runtime by
# default so a delete cannot reach the real listener. Tests that care about the rebuild
# override the spy themselves.
@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_database(tmp_path / "telebuba.db")

    async def _noop() -> None:
        return None

    monkeypatch.setattr(campaigns._runtime, "reconcile_if_running", _noop)


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


def _insert_comment(campaign_id: str, channel: str, post_id: int, status: str) -> None:
    with _get_engine().begin() as conn:
        conn.execute(
            insert(_neurocomment_comments).values(
                channel=channel,
                post_id=post_id,
                campaign_id=campaign_id,
                account_id="acc-1",
                status=status,
                created_at="2026-06-25T10:00:00Z",
                updated_at="2026-06-25T10:00:00Z",
            ),
        )


async def _deletion_log_entries() -> list[LogEntry]:
    return [
        entry
        for entry in await list_recent_logs(limit=50)
        if entry.event == "neurocomment_campaign_deleted"
    ]


@pytest.mark.asyncio
async def test_delete_campaign_reconciles_after_the_rows_are_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The listener is re-pointed, and only once the DB no longer holds the campaign's links.

    Order is the whole fix, not the call: reconcile rebuilds the watch set by re-reading the
    DB, so running it before the DELETE resubscribes to the very links about to vanish and
    the listener goes on awaiting posts from them. The live stand showed the other half of
    it — no reconcile at all, and a listener that kept logging a miss per post until restart.
    """
    campaign = await campaigns.create_campaign(CampaignCreate(name="D", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@chan")
    order: list[str] = []
    watched: list[int] = []
    real_delete = campaigns.db.delete_campaign

    async def _delete(campaign_id: str) -> None:
        order.append("delete")
        await real_delete(campaign_id)

    async def _reconcile() -> None:
        order.append("reconcile")
        links = (await campaigns.list_campaign_channels(campaign.campaign_id)).links
        watched.append(len(links))

    monkeypatch.setattr(campaigns.db, "delete_campaign", _delete)
    monkeypatch.setattr(campaigns._runtime, "reconcile_if_running", _reconcile)

    await campaigns.delete_campaign(campaign.campaign_id)

    assert order == ["delete", "reconcile"]
    # What reconcile would have re-subscribed to: nothing. Asserted on the DB it reads,
    # because the call order alone would still pass if the delete were not yet committed.
    assert watched == [0]


@pytest.mark.asyncio
async def test_delete_campaign_does_not_wake_a_stopped_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A remembered-but-paused listener stays down — the gate stays inside reconcile_if_running.

    Deleting a campaign must not be a back door to Start: the delete calls the same gated
    helper its five neighbours do, and the real one (not the fixture's stub) is put back here
    so the persisted ``listener_running`` flag is what decides.
    """
    reconciled: list[str] = []

    async def _reconcile_runtime(account_id: str) -> None:
        reconciled.append(account_id)

    monkeypatch.setattr(
        campaigns._runtime,
        "reconcile_if_running",
        _lifecycle.reconcile_if_running,
    )
    monkeypatch.setattr(campaigns._runtime, "reconcile_neurocomment_runtime", _reconcile_runtime)
    await set_listener_account_id("listener-1")

    campaign = await campaigns.create_campaign(CampaignCreate(name="D", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@chan")
    await campaigns.delete_campaign(campaign.campaign_id)

    assert reconciled == []


@pytest.mark.asyncio
async def test_delete_campaign_logs_what_it_destroyed() -> None:
    """One WARNING line carrying the counts, read while the rows still existed.

    The operator deleted a campaign on the live stand and the log held not one line about
    it — the 681 erased comments could only be reconstructed by reading the database.
    """
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await campaigns.create_campaign(CampaignCreate(name="D", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@a")
    await campaigns.link_channel(campaign.campaign_id, "@b")
    await campaigns.link_channel(campaign.campaign_id, "@gone")
    await campaigns.deactivate_channel(campaign.campaign_id, "@gone")
    await campaigns.assign_account_to_campaign(campaign.campaign_id, "acc-1")
    # Mixed statuses: the count is of history, not of quota, so a failed row counts too.
    _insert_comment(campaign.campaign_id, "@a", 1, "posted")
    _insert_comment(campaign.campaign_id, "@a", 2, "failed")
    _insert_comment(campaign.campaign_id, "@b", 3, "waiting")

    await campaigns.delete_campaign(campaign.campaign_id)

    entries = await _deletion_log_entries()
    assert len(entries) == 1
    assert entries[0].level == "WARNING"
    # ``active_channels``, not ``channels``: the DELETE also takes the deactivated link, the
    # account assignments and the discovery candidates, so the bare key promised a total.
    assert entries[0].extra == {
        "campaign_id": campaign.campaign_id,
        "active_channels": 2,  # @gone was already deactivated — never in the watch set
        "comments": 3,
    }


@pytest.mark.asyncio
async def test_delete_campaign_logs_zeros_for_an_empty_campaign() -> None:
    """An empty campaign that REALLY existed is logged with zeros — silence would be the bug.

    Mirror of ``test_delete_campaign_says_nothing_about_an_id_that_was_not_there``: the same
    zeros are honest here and invented there, and only the existence read separates them.
    """
    campaign = await campaigns.create_campaign(CampaignCreate(name="Empty", prompt="p"))

    await campaigns.delete_campaign(campaign.campaign_id)

    entries = await _deletion_log_entries()
    assert len(entries) == 1
    assert entries[0].extra == {
        "campaign_id": campaign.campaign_id,
        "active_channels": 0,
        "comments": 0,
    }


@pytest.mark.asyncio
async def test_delete_campaign_says_nothing_about_an_id_that_was_not_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown id stays a quiet success, but writes no record of destroying nothing.

    A ``0/0`` line is indistinguishable from really deleting an empty campaign, so the
    double-click the UI allows read as two campaigns gone — ``1/1`` then ``0/0``.
    """
    calls: list[str] = []

    async def _reconcile() -> None:
        calls.append("reconcile")

    monkeypatch.setattr(campaigns._runtime, "reconcile_if_running", _reconcile)

    await campaigns.delete_campaign("does-not-exist-at-all")  # idempotent: still no raise

    assert await _deletion_log_entries() == []
    assert calls == []  # nothing was unsubscribed, so nothing needs re-pointing


@pytest.mark.asyncio
async def test_deleting_the_same_campaign_twice_logs_one_line() -> None:
    """One campaign destroyed, one line: the second click has nothing left to report."""
    campaign = await campaigns.create_campaign(CampaignCreate(name="D", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@chan")

    await campaigns.delete_campaign(campaign.campaign_id)
    await campaigns.delete_campaign(campaign.campaign_id)

    entries = await _deletion_log_entries()
    assert len(entries) == 1
    assert entries[0].extra == {
        "campaign_id": campaign.campaign_id,
        "active_channels": 1,
        "comments": 0,
    }


@pytest.mark.asyncio
async def test_delete_campaign_survives_a_failing_reconcile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reconcile runs after the commit, so its failure must not report an error for it.

    It raises a Telegram client (no session, a flood wait) and the route has no handler, so
    the operator met a 500 over a campaign already gone and already logged as deleted — and
    the retry that followed wrote the phantom second line.
    """
    campaign = await campaigns.create_campaign(CampaignCreate(name="D", prompt="p"))
    await campaigns.link_channel(campaign.campaign_id, "@chan")

    async def _boom() -> None:
        msg = "no session for the listener"
        raise RuntimeError(msg)

    monkeypatch.setattr(campaigns._runtime, "reconcile_if_running", _boom)

    await campaigns.delete_campaign(campaign.campaign_id)  # must not raise

    assert (await campaigns.list_campaigns()).campaigns == []
    assert len(await _deletion_log_entries()) == 1  # the delete still reports itself once
    event = "neurocomment_campaign_delete_reconcile_failed"
    failures = [entry for entry in await list_recent_logs(limit=50) if entry.event == event]
    # Visible, and by exception TYPE only — a gateway's text can carry a session path.
    assert [entry.extra for entry in failures] == [
        {"campaign_id": campaign.campaign_id, "error_type": "RuntimeError"},
    ]
