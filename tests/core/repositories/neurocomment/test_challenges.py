"""Neurocomment challenge repository tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    count_by_outcome,
    evict_cached_decision,
    insert_challenge,
    list_failed_for_channel,
    list_failed_for_channels,
    lookup_cached_decision,
    resolve_pending_outcome,
)
from schemas.challenge import ChallengeDecision, ChallengeInsert


@pytest.mark.asyncio
async def test_insert_challenge_and_list_failed_for_channel() -> None:
    await insert_challenge(
        ChallengeInsert(
            challenge_hash="h1",
            account_id="acc-1",
            channel="@chan",
            raw_text="нажми, чтобы остаться",
            button_labels=["Я не бот", "Я бот"],
            outcome="give_up",
        ),
    )

    result = await list_failed_for_channel("@chan", limit=10)

    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.account_id == "acc-1"
    assert row.channel == "@chan"
    assert row.raw_text == "нажми, чтобы остаться"
    assert row.button_labels == ["Я не бот", "Я бот"]
    assert row.outcome == "give_up"


@pytest.mark.asyncio
async def test_list_failed_for_channel_excludes_solved() -> None:
    await insert_challenge(
        ChallengeInsert(
            challenge_hash="h1",
            account_id="acc-1",
            channel="@chan",
            raw_text="solved one",
            button_labels=["ok"],
            outcome="solved",
        ),
    )

    result = await list_failed_for_channel("@chan", limit=10)

    assert result.rows == []


@pytest.mark.asyncio
async def test_list_failed_for_channel_is_newest_first_and_limited() -> None:
    for i in range(3):
        await insert_challenge(
            ChallengeInsert(
                challenge_hash=f"h{i}",
                account_id="acc-1",
                channel="@chan",
                raw_text=f"challenge {i}",
                button_labels=["x"],
                outcome="give_up",
            ),
        )

    result = await list_failed_for_channel("@chan", limit=2)

    # Newest-first (id desc tiebreaker), capped at the limit.
    assert [r.raw_text for r in result.rows] == ["challenge 2", "challenge 1"]


@pytest.mark.asyncio
async def test_list_failed_for_channels_filters_orders_and_limits() -> None:
    # Insert in a deterministic order so id (the tiebreaker) tracks insert order.
    for challenge_hash, channel, outcome in (
        ("h0", "@a", "give_up"),  # oldest
        ("h1", "@b", "failed"),
        ("h2", "@a", "solved"),  # solved → excluded
        ("h3", "@c", "failed"),  # channel outside the queried set → excluded
        ("h4", "@b", "give_up"),  # newest of the queried, unsolved rows
    ):
        await insert_challenge(
            ChallengeInsert(
                challenge_hash=challenge_hash,
                account_id="acc-1",
                channel=channel,
                raw_text=challenge_hash,
                button_labels=["x"],
                outcome=outcome,
            ),
        )

    result = await list_failed_for_channels(["@a", "@b"], limit=10)

    # Only unsolved rows on the queried channels, newest first.
    assert [r.raw_text for r in result.rows] == ["h4", "h1", "h0"]

    # The global limit caps the merged result, keeping the newest.
    limited = await list_failed_for_channels(["@a", "@b"], limit=2)
    assert [r.raw_text for r in limited.rows] == ["h4", "h1"]

    # No channels → empty, no query.
    assert (await list_failed_for_channels([], limit=10)).rows == []


def _solved_insert(
    challenge_hash: str, account_id: str, decision: ChallengeDecision
) -> ChallengeInsert:
    return ChallengeInsert(
        challenge_hash=challenge_hash,
        account_id=account_id,
        channel="@chan",
        raw_text="prove human",
        button_labels=["yes"],
        outcome="solved",
        decision_json=decision.model_dump_json(),
    )


@pytest.mark.asyncio
async def test_lookup_cached_decision_returns_solved_decision() -> None:
    decision = ChallengeDecision(
        action="click_button", button_index=2, confidence=0.8, reasoning="r"
    )
    await insert_challenge(_solved_insert("hash-1", "acc-1", decision))

    cached = await lookup_cached_decision("hash-1")

    assert cached is not None
    assert cached.action == "click_button"
    assert cached.button_index == 2


@pytest.mark.asyncio
async def test_evict_cached_decision_removes_only_solved_rows() -> None:
    decision = ChallengeDecision(
        action="click_button", button_index=0, confidence=0.9, reasoning="r"
    )
    await insert_challenge(_solved_insert("hash-evict", "acc-1", decision))
    # A non-solved audit row under the same hash must survive (not a cache entry).
    await insert_challenge(
        ChallengeInsert(
            challenge_hash="hash-evict",
            account_id="acc-2",
            channel="@chan",
            raw_text="prove human",
            button_labels=["yes"],
            outcome="give_up",
        ),
    )

    removed = await evict_cached_decision("hash-evict")

    assert removed == 1
    assert await lookup_cached_decision("hash-evict") is None  # cache row gone
    # The give_up audit row is untouched.
    assert len((await list_failed_for_channel("@chan", limit=10)).rows) == 1


@pytest.mark.asyncio
async def test_lookup_cached_decision_ignores_non_solved() -> None:
    await insert_challenge(
        ChallengeInsert(
            challenge_hash="hash-2",
            account_id="acc-1",
            channel="@chan",
            raw_text="x",
            button_labels=["y"],
            outcome="give_up",
        ),
    )

    assert await lookup_cached_decision("hash-2") is None


@pytest.mark.asyncio
async def test_resolve_pending_outcome_marks_latest_pending() -> None:
    await insert_challenge(
        ChallengeInsert(
            challenge_hash="h",
            account_id="acc-1",
            channel="@chan",
            raw_text="x",
            button_labels=["y"],
            outcome="pending",
            decision_json=ChallengeDecision(
                action="click_button", button_index=0, confidence=0.9, reasoning="r"
            ).model_dump_json(),
        ),
    )

    await resolve_pending_outcome("acc-1", "@chan", "solved")

    engine = _get_engine()
    with engine.connect() as connection:
        row = (
            connection.exec_driver_sql(
                "SELECT outcome, outcome_at FROM neurocomment_challenges WHERE account_id='acc-1'",
            )
            .mappings()
            .first()
        )
    assert row is not None
    assert row["outcome"] == "solved"
    assert row["outcome_at"] is not None


@pytest.mark.asyncio
async def test_resolve_pending_outcome_is_noop_without_pending() -> None:
    # No pending row for the pair → must not raise.
    await resolve_pending_outcome("ghost", "@chan", "failed")


@pytest.mark.asyncio
async def test_resolve_pending_outcome_is_winner_takes_all() -> None:
    # A pending row resolves exactly once: the first call wins (True), a second
    # sees no still-pending row and returns False, so its challenge-counter side
    # effect never double-fires.
    await insert_challenge(
        ChallengeInsert(
            challenge_hash="h",
            account_id="acc-1",
            channel="@chan",
            raw_text="x",
            button_labels=["y"],
            outcome="pending",
            decision_json=None,
        ),
    )

    assert await resolve_pending_outcome("acc-1", "@chan", "solved") is True
    assert await resolve_pending_outcome("acc-1", "@chan", "failed") is False


@pytest.mark.asyncio
async def test_count_by_outcome_groups_and_windows() -> None:
    for outcome in ("solved", "solved", "failed", "give_up"):
        await insert_challenge(
            ChallengeInsert(
                challenge_hash="h",
                account_id="acc-1",
                channel="@chan",
                raw_text="x",
                button_labels=["y"],
                outcome=outcome,
            ),
        )

    counts = await count_by_outcome(["@chan"], since="")
    assert (counts.solved, counts.failed, counts.give_up, counts.pending) == (2, 1, 1, 0)
    # A future lower bound excludes everything.
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    empty = await count_by_outcome(["@chan"], since=future)
    assert (empty.solved, empty.failed, empty.give_up) == (0, 0, 0)
    # A channel outside the set is not counted.
    assert (await count_by_outcome(["@other"], since="")).solved == 0


@pytest.mark.asyncio
async def test_list_failed_for_channel_surfaces_reasoning() -> None:
    decision = ChallengeDecision(action="give_up", confidence=0.4, reasoning="image captcha")
    await insert_challenge(
        ChallengeInsert(
            challenge_hash="h",
            account_id="acc-1",
            channel="@chan",
            raw_text="x",
            button_labels=["y"],
            outcome="give_up",
            decision_json=decision.model_dump_json(),
        ),
    )

    rows = (await list_failed_for_channel("@chan", limit=10)).rows
    assert rows[0].reasoning == "image captcha"
