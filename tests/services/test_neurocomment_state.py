"""Unit tests for the in-memory channel state in ``services.neurocomment._state``.

Two mechanisms live there: the write-failure *window* that decides when a
"this channel will not let us write" round ends (#147 — the round counter and the
pause deadline it feeds are persisted, and covered in
``tests/services/neurocomment/test_channel_pause.py``), and the deletion sweep's own
escalating back-off, which is unchanged and still entirely in memory.
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


# --------------------------------------------------------------------------- #
# Deletion back-off — episode-scoped counting (L1): the same vanished comments
# must trip once, not re-escalate every cooldown as they linger in the window.
# --------------------------------------------------------------------------- #


def _register(
    channel: str,
    now: datetime,
    *,
    window_ids: set[int],
    missing_ids: set[int],
    min_deletions: int = 2,
) -> float | None:
    return _state.register_channel_deletions(
        channel,
        now,
        _state.ChannelDeletionScan(window_ids=window_ids, missing_ids=missing_ids),
        min_deletions=min_deletions,
        base_seconds=_BASE,
        max_seconds=_MAX,
    )


def test_new_deletion_episode_trips_at_base() -> None:
    seconds = _register("@c", _NOW, window_ids={1, 2, 3}, missing_ids={1, 2})

    assert seconds == _BASE
    assert _state._CHANNEL_TRIPS["@c"] == 1
    assert _state.channel_in_backoff("@c", _NOW) is True


def test_same_episode_across_lapsed_cooldowns_does_not_re_escalate() -> None:
    # First sweep: 2 comments gone → trip at base.
    assert _register("@c", _NOW, window_ids={1, 2, 3}, missing_ids={1, 2}) == _BASE

    # The cooldown lapses; the SAME comments still linger in the lookback window.
    # Re-counting them must NOT re-escalate — the episode was already counted.
    later = _NOW + timedelta(seconds=_BASE + 1)
    assert _register("@c", later, window_ids={1, 2, 3}, missing_ids={1, 2}) is None
    # A clean window (no genuinely new deletions) lets the escalation memory decay.
    assert _state._CHANNEL_TRIPS.get("@c", 0) == 0


def test_genuinely_new_deletions_escalate_to_double() -> None:
    assert _register("@c", _NOW, window_ids={1, 2, 3}, missing_ids={1, 2}) == _BASE

    # A fresh batch of comments vanishes (ids not previously counted) → escalate.
    second = _register("@c", _NOW, window_ids={1, 2, 3, 4, 5}, missing_ids={4, 5})

    assert second == _BASE * 2


def test_counted_ids_pruned_when_they_age_out_of_window() -> None:
    _register("@c", _NOW, window_ids={1, 2}, missing_ids={1, 2})
    assert _state._CHANNEL_COUNTED_DELETED["@c"] == {1, 2}

    # The lookback window slides forward: ids 1,2 are no longer in it. The counted
    # set is pruned to the current window so it never grows without bound.
    _register("@c", _NOW, window_ids={3, 4}, missing_ids=set())

    assert _state._CHANNEL_COUNTED_DELETED["@c"] == set()
