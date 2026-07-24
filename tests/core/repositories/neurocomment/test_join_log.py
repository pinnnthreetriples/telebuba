"""Neurocomment join-log repository tests — record + rolling-window count."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    count_account_joins_since,
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
