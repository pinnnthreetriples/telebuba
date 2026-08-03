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

    assert await mark_watch_channel_join_lost("acc-1", "@a") == 1

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

    assert await mark_watch_channel_join_lost("acc-1", "@a") == 1
    assert await mark_watch_channel_join_lost("acc-1", "@a") is None
    # Nor is there anything to charge when the pair never landed a join row at all.
    assert await mark_watch_channel_join_lost("acc-1", "@never") is None


@pytest.mark.asyncio
async def test_a_lost_join_never_widens_to_the_group_joins() -> None:
    """``watch_channel`` NULL renders ``IS NULL``, which would swallow every group join."""
    await record_join("acc-1")
    await record_join("acc-1")

    assert await mark_watch_channel_join_lost("acc-1", "") is None
    assert await list_exhausted_watch_channels("acc-1", 1) == set()


@pytest.mark.asyncio
async def test_exhausted_channels_are_the_ones_with_no_attempts_left() -> None:
    """The attempt counter is the pair's own lost rows, so it survives a restart."""
    await record_join("acc-1", watch_channel="@a")
    await mark_watch_channel_join_lost("acc-1", "@a")
    await record_join("acc-1", watch_channel="@b")
    await mark_watch_channel_join_lost("acc-1", "@b")
    await record_join("acc-1", watch_channel="@b")  # the re-join
    assert await mark_watch_channel_join_lost("acc-1", "@b") == 2

    assert await list_exhausted_watch_channels("acc-1", 2) == {"@b"}
    assert await list_exhausted_watch_channels("acc-1", 3) == set()
    assert await list_exhausted_watch_channels("acc-2", 2) == set()
