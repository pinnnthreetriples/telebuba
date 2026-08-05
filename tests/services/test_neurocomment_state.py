"""Unit tests for the in-memory channel state in ``services.neurocomment._state``.

One mechanism lives there: the write-failure *window* that decides when a
"this channel will not let us write" round ends (#147 — the round counter and the
pause deadline it feeds are persisted, and covered in
``tests/services/neurocomment/test_channel_pause.py``). The deletion sweep used to keep
an escalating back-off here too; deletions now only get recorded, so it is gone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from services.neurocomment import _state

_NOW = datetime(2026, 6, 24, tzinfo=UTC)
_BASE = 100.0
_MAX = 400.0


@pytest.fixture(autouse=True)
def _reset() -> None:
    _state.reset_for_tests()


def _fail(channel: str, *, k: int = 3) -> bool:
    return _state.register_write_failure(channel, min_failures=k)


def test_below_k_failures_does_not_close_a_round() -> None:
    assert _fail("@c") is False
    assert _fail("@c") is False


def test_kth_failure_closes_the_round() -> None:
    _fail("@c")
    _fail("@c")

    assert _fail("@c") is True


def test_window_restarts_after_a_closed_round() -> None:
    # The K counter zeroes on the round it closes, so the next round needs K more
    # failures — the escalation that used to live here is now four flat rounds in the DB.
    for _ in range(3):
        _fail("@c")

    assert _fail("@c") is False
    assert _fail("@c") is False
    assert _fail("@c") is True


def test_reset_zeroes_the_failure_window() -> None:
    # A delivered comment resets the K counter, so sporadic failures spread across many
    # successes never accumulate to K and pause a mostly-working channel.
    assert _fail("@c") is False  # 1 failure
    _state.reset_write_failures("@c")
    assert _fail("@c") is False  # counter restarted at 1, not 2
    _state.reset_write_failures("@c")
    assert _fail("@c") is False  # still 1, not 3


def test_k_consecutive_failures_still_close_a_round_after_reset() -> None:
    _state.reset_write_failures("@c")  # reset on a clean channel is a no-op
    _fail("@c")
    _fail("@c")

    assert _fail("@c") is True


def test_channel_paused_reads_the_persisted_deadline() -> None:
    assert _state.channel_paused(None, _NOW) is False
    assert _state.channel_paused((_NOW + timedelta(hours=1)).isoformat(), _NOW) is True
    # Expiry needs no sweep: the channel is simply tried again by the next post.
    assert _state.channel_paused((_NOW - timedelta(seconds=1)).isoformat(), _NOW) is False
