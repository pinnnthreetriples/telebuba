"""Neurocomment retention-purge repository tests.

The three append-only tables (comments / challenges / join log) were unbounded; the
purge trims them by age, but two exclusions are load-bearing and get their own tests:
an in-flight ``claimed`` comment (the idempotency claim) and a ``solved`` challenge row
(the global decision cache) must survive any cutoff.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    claim_comment,
    count_account_joins_since,
    create_account,
    create_campaign,
    fetch_comment,
    insert_challenge,
    lookup_cached_decision,
    mark_comment_failed,
    mark_comment_posted,
    purge_neurocomment_history_older_than,
    record_join,
)
from schemas.accounts import AccountCreate
from schemas.challenge import ChallengeDecision, ChallengeInsert
from schemas.neurocomment import CampaignCreate

_ANCIENT = datetime(2020, 1, 1, tzinfo=UTC)


def _backdate(table: str, column: str) -> None:
    """Test-only: push every row of ``table`` far behind any cutoff we then pass."""
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            f"UPDATE {table} SET {column} = ?",  # noqa: S608 - literal names from this module
            (_ANCIENT.isoformat(),),
        )


def _cutoff() -> str:
    return (datetime.now(UTC) - timedelta(days=90)).isoformat()


async def _campaign_with_comments() -> None:
    """One posted, one failed and one still-claimed comment, all stamped long ago."""
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    await claim_comment("@chan", 1, campaign.campaign_id, "acc-1")
    await mark_comment_posted("@chan", 1, comment_text="x", comment_msg_id=11)
    await claim_comment("@chan", 2, campaign.campaign_id, "acc-1")
    await mark_comment_failed("@chan", 2)
    await claim_comment("@chan", 3, campaign.campaign_id, "acc-1")  # stays 'claimed'
    _backdate("neurocomment_comments", "created_at")


@pytest.mark.asyncio
async def test_purge_removes_settled_comments_and_keeps_in_flight_claims() -> None:
    await _campaign_with_comments()

    removed = await purge_neurocomment_history_older_than(_cutoff())

    assert removed == 2  # posted + failed
    assert await fetch_comment("@chan", 1) is None
    assert await fetch_comment("@chan", 2) is None
    # A 'claimed' row is an in-flight post claim: dropping it would free its
    # (channel, post_id) key for a duplicate comment and starve the stale-claim reclaim.
    still_claimed = await fetch_comment("@chan", 3)
    assert still_claimed is not None
    assert still_claimed.status == "claimed"


@pytest.mark.asyncio
async def test_purge_keeps_fresh_rows() -> None:
    """Rows inside the window are untouched — the cutoff, not the table, decides."""
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    await claim_comment("@chan", 1, campaign.campaign_id, "acc-1")
    await mark_comment_posted("@chan", 1, comment_text="x", comment_msg_id=11)
    await record_join("acc-1")

    assert await purge_neurocomment_history_older_than(_cutoff()) == 0
    assert await fetch_comment("@chan", 1) is not None


async def _insert(outcome: str, challenge_hash: str) -> None:
    await insert_challenge(
        ChallengeInsert(
            challenge_hash=challenge_hash,
            account_id="acc-1",
            channel="@chan",
            raw_text="prove you are human",
            button_labels=["yes", "no"],
            outcome=outcome,
            decision_json=ChallengeDecision(
                action="click_button",
                button_index=0,
                confidence=0.9,
                reasoning="r",
            ).model_dump_json(),
        ),
    )


@pytest.mark.asyncio
async def test_purge_keeps_solved_challenges_and_drops_the_rest() -> None:
    await _insert("solved", "h-solved")
    await _insert("give_up", "h-giveup")
    await _insert("failed", "h-failed")
    await _insert("pending", "h-pending")
    _backdate("neurocomment_challenges", "decided_at")

    removed = await purge_neurocomment_history_older_than(_cutoff())

    assert removed == 3  # give_up + failed + pending
    # The solved rows ARE the global decision cache the solver reads before paying for a
    # fresh LLM call — purging them by age would silently evict it.
    assert await lookup_cached_decision("h-solved") is not None
    assert await lookup_cached_decision("h-giveup") is None


@pytest.mark.asyncio
async def test_purge_removes_old_join_log_rows() -> None:
    await record_join("acc-1")
    await record_join("acc-1")
    _backdate("neurocomment_join_log", "joined_at")

    removed = await purge_neurocomment_history_older_than(_cutoff())

    assert removed == 2
    epoch = datetime(2000, 1, 1, tzinfo=UTC).isoformat()
    assert await count_account_joins_since("acc-1", epoch) == 0


@pytest.mark.asyncio
async def test_purge_sums_rows_across_all_three_tables() -> None:
    await _campaign_with_comments()
    await _insert("give_up", "h-giveup")
    _backdate("neurocomment_challenges", "decided_at")
    await record_join("acc-1")
    _backdate("neurocomment_join_log", "joined_at")

    # 2 settled comments + 1 give_up challenge + 1 join row, counted as one int.
    assert await purge_neurocomment_history_older_than(_cutoff()) == 4
