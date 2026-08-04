"""Neurocomment join-log repository tests — record + rolling-window count."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    count_account_joins_since,
    list_exhausted_watch_channels,
    list_joined_watch_channels,
    mark_watch_channel_join_lost,
    record_join,
)


async def _backdate_join(account_id: str, when: datetime) -> None:
    """Test-only: rewrite an account's join rows to ``when`` (mirrors the quota idiom)."""
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_join_log SET joined_at = ? WHERE account_id = ?",
            (when.isoformat(), account_id),
        )


async def _backdate_loss(account_id: str, watch_channel: str, when: datetime) -> None:
    """Test-only: age a pair's already-stamped losses, so an attempt window can expire.

    Each row is aged to its OWN timestamp. Collapsing them all onto one instant would make
    the window filter untestable: the count is over DISTINCT ``lost_at``, so two losses
    sharing a timestamp read as one attempt whether the window is applied or not.
    """
    with _get_engine().begin() as connection:
        ids = [
            int(row[0])
            for row in connection.exec_driver_sql(
                "SELECT id FROM neurocomment_join_log WHERE account_id = ? "
                "AND watch_channel = ? AND lost_at IS NOT NULL ORDER BY id",
                (account_id, watch_channel),
            )
        ]
        for offset, row_id in enumerate(ids):
            connection.exec_driver_sql(
                "UPDATE neurocomment_join_log SET lost_at = ? WHERE id = ?",
                ((when + timedelta(minutes=offset)).isoformat(), row_id),
            )


def _window_start(hours: float = 168.0) -> str:
    """The attempt window the listener pass would compute — the production default."""
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


@pytest.mark.asyncio
async def test_record_and_count_joins_in_window() -> None:
    await record_join("acc-1")
    await record_join("acc-1")
    await record_join("acc-2")

    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert await count_account_joins_since("acc-1", past) == 2
    assert await count_account_joins_since("acc-2", past) == 1
    assert await count_account_joins_since("ghost", past) == 0
    # A window that starts in the future counts nothing (upper boundary sanity).
    assert await count_account_joins_since("acc-1", future) == 0


@pytest.mark.asyncio
async def test_join_older_than_window_is_not_counted() -> None:
    """A join stamped before the 24h cutoff falls outside the rolling window."""
    await record_join("acc-1")
    # Push the join to just over 24h ago — the daily window must exclude it.
    await _backdate_join("acc-1", datetime.now(UTC) - timedelta(hours=24, minutes=1))

    day_ago = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    assert await count_account_joins_since("acc-1", day_ago) == 0

    # A fresh join lands back inside the window.
    await record_join("acc-1")
    assert await count_account_joins_since("acc-1", day_ago) == 1


@pytest.mark.asyncio
async def test_marking_a_join_lost_touches_only_that_pair() -> None:
    """A kick disproves one account's membership of one channel — nothing else."""
    await record_join("acc-1", watch_channel="@a")
    await record_join("acc-1", watch_channel="@b")
    await record_join("acc-2", watch_channel="@a")
    await record_join("acc-1")  # a discussion-group join carries no watch channel

    assert await mark_watch_channel_join_lost("acc-1", "@a", _window_start()) == 1

    assert await list_joined_watch_channels("acc-1") == {"@b"}
    assert await list_joined_watch_channels("acc-2") == {"@a"}
    # The rolling-24h count does NOT drop: the join RPC was spent, and the anti-freeze cap
    # is a budget of RPCs. Deleting the row instead made the cap unreachable for a channel
    # that keeps failing — one row out, one row in, net zero, for ever.
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    assert await count_account_joins_since("acc-1", past) == 3


@pytest.mark.asyncio
async def test_marking_a_join_lost_is_idempotent_within_one_report() -> None:
    """Both refill sites report the same loss in one pass; only the first spends an attempt."""
    await record_join("acc-1", watch_channel="@a")

    window = _window_start()
    assert await mark_watch_channel_join_lost("acc-1", "@a", window) == 1
    assert await mark_watch_channel_join_lost("acc-1", "@a", window) is None
    # Nor is there anything to charge when the pair never landed a join row at all.
    assert await mark_watch_channel_join_lost("acc-1", "@never", window) is None


@pytest.mark.asyncio
async def test_a_lost_join_never_widens_to_the_group_joins() -> None:
    """A ``str`` watch channel compares by equality, which no NULL group-join row matches."""
    await record_join("acc-1")
    await record_join("acc-1")

    assert await mark_watch_channel_join_lost("acc-1", "", _window_start()) is None
    assert await list_exhausted_watch_channels("acc-1", 1, _window_start()) == set()


@pytest.mark.asyncio
async def test_exhausted_channels_are_the_ones_with_no_attempts_left() -> None:
    """The attempt counter is the pair's own lost rows, so it survives a restart."""
    window = _window_start()
    await record_join("acc-1", watch_channel="@a")
    await mark_watch_channel_join_lost("acc-1", "@a", window)
    await record_join("acc-1", watch_channel="@b")
    await mark_watch_channel_join_lost("acc-1", "@b", window)
    await record_join("acc-1", watch_channel="@b")  # the re-join
    assert await mark_watch_channel_join_lost("acc-1", "@b", window) == 2

    assert await list_exhausted_watch_channels("acc-1", 2, window) == {"@b"}
    assert await list_exhausted_watch_channels("acc-1", 3, window) == set()
    assert await list_exhausted_watch_channels("acc-2", 2, window) == set()


@pytest.mark.asyncio
async def test_a_pair_exhausted_outside_the_window_is_eligible_again() -> None:
    """The give-up expires: nothing ever clears ``lost_at``, so the count must be windowed.

    Without the window the count is every loss for all time, and the retention purge may
    never run (``retention_days=0`` keeps rows for ever) — so a channel lost twice, months
    apart, was silenced permanently with no in-product way back.
    """
    await record_join("acc-1", watch_channel="@a")
    await mark_watch_channel_join_lost("acc-1", "@a", _window_start())
    await record_join("acc-1", watch_channel="@a")  # the re-join
    await mark_watch_channel_join_lost("acc-1", "@a", _window_start())

    # Both losses inside the window: the budget of 2 is spent, so the pass gives up.
    assert await list_exhausted_watch_channels("acc-1", 2, _window_start()) == {"@a"}

    # Age them past the window — the same rows, still there for the join cap to count.
    await _backdate_loss("acc-1", "@a", datetime.now(UTC) - timedelta(hours=169))
    assert await list_exhausted_watch_channels("acc-1", 2, _window_start()) == set()

    # And a fresh loss re-arms the count from one, not from three.
    await record_join("acc-1", watch_channel="@a")
    assert await mark_watch_channel_join_lost("acc-1", "@a", _window_start()) == 1


@pytest.mark.asyncio
async def test_one_loss_charges_one_attempt_however_many_rows_it_stamps() -> None:
    """Two standing rows for a pair are one membership, so losing it costs one attempt.

    The UPDATE has no LIMIT (it must not: a row left unstamped still reads as "joined" and
    re-seeds the pass's skip cache), so the charge is counted over DISTINCT ``lost_at``.
    Counting rows spent the whole budget on the first loss, having sent no re-join at all.
    """
    await record_join("acc-1", watch_channel="@dup")
    await record_join("acc-1", watch_channel="@dup")

    assert await mark_watch_channel_join_lost("acc-1", "@dup", _window_start()) == 1
    # Both rows are stamped, so neither re-seeds the join cache...
    assert await list_joined_watch_channels("acc-1") == set()
    # ...and one loss has not exhausted a budget of two.
    assert await list_exhausted_watch_channels("acc-1", 2, _window_start()) == set()
