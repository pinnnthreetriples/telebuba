"""Neurocomment comment and quota repository tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    claim_comment,
    count_account_channel_comments_since,
    count_account_comments_since,
    count_channel_comments_per_account_since,
    count_comments_per_account_since,
    create_account,
    create_campaign,
    fetch_comment,
    list_posted_comments_for_channel_since,
    mark_comment_failed,
    mark_comment_posted,
    reclaim_stale_claims,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate


@pytest.mark.asyncio
async def test_comment_claim_is_idempotent_and_records_outcome() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))

    assert await claim_comment("@chan", 100, campaign.campaign_id, "acc-1") is True
    # the same post can only be claimed once across the fleet
    assert await claim_comment("@chan", 100, campaign.campaign_id, "acc-1") is False

    pending = await fetch_comment("@chan", 100)
    assert pending is not None
    assert pending.status == "claimed"

    posted = await mark_comment_posted("@chan", 100, comment_text="nice", comment_msg_id=555)
    assert posted is not None
    assert posted.status == "posted"
    assert posted.comment_text == "nice"
    assert posted.comment_msg_id == 555

    assert await claim_comment("@chan", 101, campaign.campaign_id, "acc-1") is True
    failed = await mark_comment_failed("@chan", 101)
    assert failed is not None
    assert failed.status == "failed"

    # marking a post nobody claimed yields None
    assert await mark_comment_posted("@chan", 999, comment_text="x", comment_msg_id=1) is None


@pytest.mark.asyncio
async def test_mark_comment_does_not_override_a_terminal_status() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    assert await claim_comment("@chan", 100, campaign.campaign_id, "acc-1") is True
    await mark_comment_posted("@chan", 100, comment_text="nice", comment_msg_id=555)

    # A late failure must not flip an already-posted claim back to failed.
    result = await mark_comment_failed("@chan", 100)
    assert result is not None
    assert result.status == "posted"
    assert result.comment_text == "nice"


async def _backdate_created_at(post_id: int, when: datetime) -> None:
    """Test-only: rewrite one comment's created_at (mirrors the board test's idiom)."""
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_comments SET created_at = ? WHERE post_id = ?",
            (when.isoformat(), post_id),
        )


@pytest.mark.asyncio
async def test_reclaim_stale_claims_marks_old_claimed_as_failed() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    assert await claim_comment("@chan", 1, campaign.campaign_id, "acc-1") is True
    # A claim stuck since before the cutoff (a crash mid-post left it 'claimed').
    await _backdate_created_at(1, datetime.now(UTC) - timedelta(hours=1))

    reclaimed = await reclaim_stale_claims(datetime.now(UTC).isoformat())

    assert reclaimed == 1
    row = await fetch_comment("@chan", 1)
    assert row is not None
    assert row.status == "failed"


@pytest.mark.asyncio
async def test_reclaim_stale_claims_leaves_fresh_claim_untouched() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    assert await claim_comment("@chan", 1, campaign.campaign_id, "acc-1") is True

    # Cutoff an hour in the past — a just-created claim is newer, so it is left alone.
    reclaimed = await reclaim_stale_claims((datetime.now(UTC) - timedelta(hours=1)).isoformat())

    assert reclaimed == 0
    row = await fetch_comment("@chan", 1)
    assert row is not None
    assert row.status == "claimed"


@pytest.mark.asyncio
async def test_reclaim_stale_claims_leaves_posted_untouched() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    assert await claim_comment("@chan", 1, campaign.campaign_id, "acc-1") is True
    await mark_comment_posted("@chan", 1, comment_text="nice", comment_msg_id=5)
    # Old, but terminal (posted) — the reclaim only touches rows still 'claimed'.
    await _backdate_created_at(1, datetime.now(UTC) - timedelta(hours=1))

    reclaimed = await reclaim_stale_claims(datetime.now(UTC).isoformat())

    assert reclaimed == 0
    row = await fetch_comment("@chan", 1)
    assert row is not None
    assert row.status == "posted"


# --------------------------------------------------------------------------- #
# Engine helpers (issue #118): throughput windows + listener watch set.
# --------------------------------------------------------------------------- #


async def _post_one(channel: str, post_id: int, campaign_id: str, account_id: str) -> None:
    assert await claim_comment(channel, post_id, campaign_id, account_id) is True
    await mark_comment_posted(channel, post_id, comment_text="hi", comment_msg_id=post_id)


@pytest.mark.asyncio
async def test_count_account_comments_since_counts_claimed_and_posted_in_window() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))

    await _post_one("@chan", 1, campaign.campaign_id, "acc-1")
    await _post_one("@chan", 2, campaign.campaign_id, "acc-1")
    # An in-flight claim counts too: it must consume quota immediately so a burst
    # arriving inside the reply-delay window can't stack past the cap.
    assert await claim_comment("@chan", 3, campaign.campaign_id, "acc-1") is True

    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert await count_account_comments_since("acc-1", past) == 3
    assert await count_account_comments_since("acc-1", future) == 0
    assert await count_account_comments_since("other", past) == 0


@pytest.mark.asyncio
async def test_count_account_channel_comments_since_is_channel_scoped() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))

    await _post_one("@one", 1, campaign.campaign_id, "acc-1")
    await _post_one("@one", 2, campaign.campaign_id, "acc-1")
    await _post_one("@two", 3, campaign.campaign_id, "acc-1")

    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    assert await count_account_channel_comments_since("acc-1", "@one", past) == 2
    assert await count_account_channel_comments_since("acc-1", "@two", past) == 1
    assert await count_account_channel_comments_since("acc-1", "@none", past) == 0


@pytest.mark.asyncio
async def test_bulk_per_account_counts_match_per_account_readers() -> None:
    # The bulk grouped readers (Tier 2 selection) must agree with the trusted
    # per-account readers they replace — account-wide and per-channel.
    for acc in ("acc-1", "acc-2"):
        await create_account(AccountCreate(account_id=acc, label=acc, session_name=acc))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))

    await _post_one("@one", 1, campaign.campaign_id, "acc-1")
    await _post_one("@one", 2, campaign.campaign_id, "acc-1")
    await _post_one("@two", 3, campaign.campaign_id, "acc-1")
    await _post_one("@one", 4, campaign.campaign_id, "acc-2")
    # An in-flight claim (claimed, not yet posted) consumes quota too.
    assert await claim_comment("@one", 5, campaign.campaign_id, "acc-2") is True

    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    grouped = await count_comments_per_account_since(past)
    per_account = {c.account_id: c.count for c in grouped.counts}
    assert per_account == {"acc-1": 3, "acc-2": 2}
    for acc in ("acc-1", "acc-2", "ghost"):
        assert per_account.get(acc, 0) == await count_account_comments_since(acc, past)

    channel_grouped = await count_channel_comments_per_account_since("@one", past)
    per_channel = {c.account_id: c.count for c in channel_grouped.counts}
    assert per_channel == {"acc-1": 2, "acc-2": 2}
    for acc in ("acc-1", "acc-2", "ghost"):
        expected = await count_account_channel_comments_since(acc, "@one", past)
        assert per_channel.get(acc, 0) == expected


@pytest.mark.asyncio
async def test_list_posted_comments_for_channel_is_scoped_to_channel_and_posted() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    await _post_one("@one", 1, campaign.campaign_id, "acc-1")
    await _post_one("@one", 2, campaign.campaign_id, "acc-1")
    await _post_one("@two", 3, campaign.campaign_id, "acc-1")
    # A claimed-but-not-posted comment is excluded (status != posted).
    assert await claim_comment("@one", 4, campaign.campaign_id, "acc-1") is True

    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    one = await list_posted_comments_for_channel_since(campaign.campaign_id, "@one", past)
    assert {c.post_id for c in one.comments} == {1, 2}
    two = await list_posted_comments_for_channel_since(campaign.campaign_id, "@two", past)
    assert {c.post_id for c in two.comments} == {3}
    none = await list_posted_comments_for_channel_since(campaign.campaign_id, "@none", past)
    assert none.comments == []
