"""Parked-post (``waiting``) repository tests — the reply-to-human-comments wait.

The theme of the whole file is that a parked post is read repeatedly by a five-minute
sweep instead of held by a live task, so every guard here is about the same failure:
two readers of one parked row both replying under one post.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    claim_comment,
    count_account_channel_comments_since,
    count_account_comments_since,
    count_comments_per_account_since,
    create_account,
    create_campaign,
    fetch_comment,
    list_waiting_comments,
    mark_comment_posted,
    park_comment,
    promote_waiting_to_claimed,
    reclaim_stale_claims,
    release_claim,
)
from core.repositories.neurocomment._waiting import ReplyStage, mark_reply_stage
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate


async def _campaign_with_account(account_id: str = "acc-1") -> str:
    await create_account(
        AccountCreate(account_id=account_id, label=account_id, session_name=account_id),
    )
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    return campaign.campaign_id


def _count_rows(channel: str, post_id: int) -> int:
    with _get_engine().connect() as connection:
        return int(
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM neurocomment_comments WHERE channel = ? AND post_id = ?",
                (channel, post_id),
            ).scalar_one(),
        )


def _backdate_created_and_updated(post_id: int, when: datetime) -> None:
    """Test-only: age a row on both stamps, the way a genuinely old row is aged."""
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_comments SET created_at = ?, updated_at = ? WHERE post_id = ?",
            (when.isoformat(), when.isoformat(), post_id),
        )


def _backdate_updated(post_id: int, when: datetime) -> None:
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_comments SET updated_at = ? WHERE post_id = ?",
            (when.isoformat(), post_id),
        )


def _reply_metadata(post_id: int) -> dict[str, object]:
    with _get_engine().connect() as connection:
        row = (
            connection.exec_driver_sql(
                "SELECT status, created_at, updated_at, reply_state, reply_stage, reply_outcome, "
                "reply_attempts, reply_deadline_at FROM neurocomment_comments WHERE post_id = ?",
                (post_id,),
            )
            .mappings()
            .one()
        )
    return dict(row)


@pytest.mark.asyncio
async def test_park_creates_a_waiting_row_and_is_idempotent() -> None:
    campaign_id = await _campaign_with_account()

    assert await park_comment("@chan", 100, campaign_id, "acc-1") is True
    parked = await fetch_comment("@chan", 100)
    assert parked is not None
    assert parked.status == "waiting"
    metadata = _reply_metadata(100)
    assert metadata["reply_state"] == "waiting"
    assert metadata["reply_stage"] == "waiting"
    assert metadata["reply_outcome"] is None
    assert metadata["reply_attempts"] == 0
    assert metadata["reply_deadline_at"] is not None

    # Same guarantee as claim_comment: a re-delivered post or a restart replaying it
    # loses the conflict rather than opening a second row for the same post.
    assert await park_comment("@chan", 100, campaign_id, "acc-1") is False
    assert _count_rows("@chan", 100) == 1


@pytest.mark.asyncio
async def test_park_and_claim_compete_for_the_same_post() -> None:
    """One post, one row, whichever verb gets there first — the modes can't both win."""
    campaign_id = await _campaign_with_account()

    assert await park_comment("@chan", 100, campaign_id, "acc-1") is True
    assert await claim_comment("@chan", 100, campaign_id, "acc-1") is False
    assert await claim_comment("@chan", 200, campaign_id, "acc-1") is True
    assert await park_comment("@chan", 200, campaign_id, "acc-1") is False

    still_parked = await fetch_comment("@chan", 100)
    assert still_parked is not None
    assert still_parked.status == "waiting"


@pytest.mark.asyncio
async def test_park_freezes_deadline_from_the_same_creation_timestamp() -> None:
    """A parked row keeps the exact deadline promised when it consumed quota."""
    campaign_id = await _campaign_with_account()
    before = datetime.now(UTC)

    assert await park_comment("@chan", 100, campaign_id, "acc-1") is True

    parked = await fetch_comment("@chan", 100)
    assert parked is not None
    created = datetime.fromisoformat(parked.created_at)
    assert before - timedelta(seconds=5) <= created <= datetime.now(UTC) + timedelta(seconds=5)
    metadata = _reply_metadata(100)
    deadline = datetime.fromisoformat(str(metadata["reply_deadline_at"]))
    assert deadline == created + timedelta(minutes=settings.neurocomment.reply_wait_minutes)


@pytest.mark.asyncio
async def test_list_waiting_comments_returns_every_parked_post_and_nothing_else() -> None:
    campaign_id = await _campaign_with_account()
    assert await park_comment("@one", 1, campaign_id, "acc-1") is True
    assert await park_comment("@two", 2, campaign_id, "acc-1") is True
    # Fleet-wide but status-scoped: an in-flight claim and a delivered comment are
    # somebody else's business and must not come back as work for the sweep.
    assert await claim_comment("@one", 3, campaign_id, "acc-1") is True
    assert await claim_comment("@one", 4, campaign_id, "acc-1") is True
    await mark_comment_posted("@one", 4, comment_text="hi", comment_msg_id=4)

    waiting = await list_waiting_comments()

    assert {(c.channel, c.post_id) for c in waiting.comments} == {("@one", 1), ("@two", 2)}
    assert all(c.status == "waiting" for c in waiting.comments)


@pytest.mark.asyncio
async def test_promote_waiting_to_claimed_is_won_by_exactly_one_caller() -> None:
    """The guard that stops two sweep ticks replying under one parked post."""
    campaign_id = await _campaign_with_account()
    assert await park_comment("@chan", 100, campaign_id, "acc-1") is True

    assert await promote_waiting_to_claimed("@chan", 100) is True
    # The second tick (or the startup sweep racing a periodic one) reads the same row
    # from list_waiting_comments and must be told the post is no longer its to send.
    assert await promote_waiting_to_claimed("@chan", 100) is False

    promoted = await fetch_comment("@chan", 100)
    assert promoted is not None
    assert promoted.status == "claimed"
    assert (await list_waiting_comments()).comments == []


@pytest.mark.asyncio
async def test_promote_ignores_rows_that_were_never_parked() -> None:
    campaign_id = await _campaign_with_account()
    assert await claim_comment("@chan", 100, campaign_id, "acc-1") is True
    assert await claim_comment("@chan", 101, campaign_id, "acc-1") is True
    await mark_comment_posted("@chan", 101, comment_text="hi", comment_msg_id=1)

    # Only 'waiting' is reachable, so aiming this at a live claim, a delivered comment
    # or an unknown post can neither hand out a claim nor rewrite an outcome.
    assert await promote_waiting_to_claimed("@chan", 100) is False
    assert await promote_waiting_to_claimed("@chan", 101) is False
    assert await promote_waiting_to_claimed("@chan", 999) is False

    delivered = await fetch_comment("@chan", 101)
    assert delivered is not None
    assert delivered.status == "posted"


@pytest.mark.asyncio
async def test_reclaim_stale_claims_never_touches_a_parked_post() -> None:
    """Regression guard: the 15-minute backstop must not cut the 10-minute wait short.

    A parked post has no worker to heartbeat it, so it looks exactly like the dead claim
    the reclaim exists to bury — but it is alive, just early. If the reclaim widened to
    ``waiting`` (or dropped the status filter), every wait longer than the stale cutoff
    would be failed out from under itself and no post would ever get its human reply.
    """
    campaign_id = await _campaign_with_account()
    assert await park_comment("@chan", 1, campaign_id, "acc-1") is True
    assert await claim_comment("@chan", 2, campaign_id, "acc-1") is True
    _backdate_created_and_updated(1, datetime.now(UTC) - timedelta(hours=1))
    _backdate_created_and_updated(2, datetime.now(UTC) - timedelta(hours=1))

    reclaimed = await reclaim_stale_claims(datetime.now(UTC).isoformat())

    # Only the claim is buried; the parked post survives its own staleness.
    assert reclaimed == 1
    parked = await fetch_comment("@chan", 1)
    assert parked is not None
    assert parked.status == "waiting"
    assert {c.post_id for c in (await list_waiting_comments()).comments} == {1}


@pytest.mark.asyncio
async def test_promotion_restarts_the_stale_clock_rather_than_inheriting_the_wait() -> None:
    """Once promoted the row is back under the reclaim — but timed from the send, not the wait."""
    campaign_id = await _campaign_with_account()
    assert await park_comment("@chan", 1, campaign_id, "acc-1") is True
    _backdate_created_and_updated(1, datetime.now(UTC) - timedelta(hours=1))

    assert await promote_waiting_to_claimed("@chan", 1) is True

    # The promotion bumped updated_at, so an hour-old wait does not arrive already stale.
    assert await reclaim_stale_claims((datetime.now(UTC) - timedelta(minutes=15)).isoformat()) == 0
    row = await fetch_comment("@chan", 1)
    assert row is not None
    assert row.status == "claimed"


@pytest.mark.asyncio
async def test_crash_after_reply_promotion_requeues_proven_pre_send_work() -> None:
    """Regression: a crash after promotion must not lose the durable parked post."""
    campaign_id = await _campaign_with_account()
    assert await park_comment("@chan", 1, campaign_id, "acc-1")
    parked = _reply_metadata(1)
    deadline = str(parked["reply_deadline_at"])
    assert await promote_waiting_to_claimed("@chan", 1)
    promoted = _reply_metadata(1)
    assert promoted["status"] == "claimed"
    assert promoted["reply_state"] == "reply_processing"
    assert promoted["reply_stage"] == "pre_send"
    assert promoted["reply_attempts"] == 1

    _backdate_updated(1, datetime.now(UTC) - timedelta(hours=1))
    assert await reclaim_stale_claims(datetime.now(UTC).isoformat()) == 1

    recovered = _reply_metadata(1)
    assert recovered["status"] == "waiting"
    assert recovered["reply_state"] == "waiting"
    assert recovered["reply_stage"] == "waiting"
    assert recovered["reply_outcome"] == "retryable"
    assert recovered["reply_attempts"] == 1
    assert recovered["created_at"] == parked["created_at"]
    assert recovered["reply_deadline_at"] == deadline
    assert {row.post_id for row in (await list_waiting_comments()).comments} == {1}

    assert await promote_waiting_to_claimed("@chan", 1)
    assert _reply_metadata(1)["reply_attempts"] == 2


@pytest.mark.asyncio
async def test_release_requeues_only_a_proven_pre_send_reply() -> None:
    campaign_id = await _campaign_with_account()
    assert await park_comment("@chan", 1, campaign_id, "acc-1")
    assert await promote_waiting_to_claimed("@chan", 1)

    await release_claim("@chan", 1)

    metadata = _reply_metadata(1)
    assert metadata["status"] == "waiting"
    assert metadata["reply_state"] == "waiting"
    assert metadata["reply_outcome"] == "retryable"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["dispatching", "dispatched"])
async def test_stale_post_dispatch_reply_is_terminal_ambiguous(stage: ReplyStage) -> None:
    campaign_id = await _campaign_with_account()
    assert await park_comment("@chan", 1, campaign_id, "acc-1")
    assert await promote_waiting_to_claimed("@chan", 1)
    assert await mark_reply_stage("@chan", 1, "dispatching")
    if stage == "dispatched":
        assert await mark_reply_stage("@chan", 1, "dispatched")
    _backdate_updated(1, datetime.now(UTC) - timedelta(hours=1))

    assert await reclaim_stale_claims(datetime.now(UTC).isoformat()) == 1

    metadata = _reply_metadata(1)
    assert metadata["status"] == "failed"
    assert metadata["reply_state"] == "terminal"
    assert metadata["reply_stage"] == stage
    assert metadata["reply_outcome"] == "ambiguous"
    assert (await list_waiting_comments()).comments == []


@pytest.mark.asyncio
async def test_reply_stage_is_monotonic_and_dispatch_cannot_be_released() -> None:
    campaign_id = await _campaign_with_account()
    assert await park_comment("@chan", 1, campaign_id, "acc-1")
    assert await promote_waiting_to_claimed("@chan", 1)
    assert await mark_reply_stage("@chan", 1, "dispatched") is False
    assert await mark_reply_stage("@chan", 1, "dispatching") is True

    await release_claim("@chan", 1)

    metadata = _reply_metadata(1)
    assert metadata["status"] == "claimed"
    assert metadata["reply_state"] == "reply_processing"
    assert metadata["reply_stage"] == "dispatching"


@pytest.mark.asyncio
async def test_quota_counts_parked_posts() -> None:
    """Ten parked posts must not read as free and then all fire through the hourly cap."""
    campaign_id = await _campaign_with_account()
    for post_id in (1, 2, 3):
        assert await park_comment("@chan", post_id, campaign_id, "acc-1") is True
    assert await claim_comment("@chan", 4, campaign_id, "acc-1") is True
    assert await claim_comment("@chan", 5, campaign_id, "acc-1") is True
    await mark_comment_posted("@chan", 5, comment_text="hi", comment_msg_id=5)

    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    assert await count_account_comments_since("acc-1", past) == 5
    assert await count_account_channel_comments_since("acc-1", "@chan", past) == 5
    grouped = await count_comments_per_account_since(["acc-1"], past)
    assert {c.account_id: c.count for c in grouped.counts} == {"acc-1": 5}

    # And the slot stays spent across the promotion — the count must not move.
    assert await promote_waiting_to_claimed("@chan", 1) is True
    assert await count_account_comments_since("acc-1", past) == 5
