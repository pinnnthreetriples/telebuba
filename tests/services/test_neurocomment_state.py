"""Unit tests for the challenge back-off state machine (Ф2 #147).

Pure in-memory escalation/self-healing logic in ``services.neurocomment._state`` —
the engine/onboarding/board integration is covered in their own test modules.
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


def _fail(channel: str, now: datetime, *, k: int = 3) -> float | None:
    return _state.register_challenge_failure(
        channel, now, min_failures=k, base_seconds=_BASE, max_seconds=_MAX
    )


def test_below_k_failures_not_in_backoff() -> None:
    assert _fail("@c", _NOW) is None
    assert _fail("@c", _NOW) is None
    assert _state.is_channel_in_challenge_backoff("@c", _NOW) is False


def test_kth_failure_trips_base_cooldown() -> None:
    _fail("@c", _NOW)
    _fail("@c", _NOW)
    seconds = _fail("@c", _NOW)

    assert seconds == _BASE
    assert _state.is_channel_in_challenge_backoff("@c", _NOW) is True


def test_register_returns_none_until_the_trip() -> None:
    assert _fail("@c", _NOW, k=2) is None
    assert _fail("@c", _NOW, k=2) == _BASE


def test_second_trip_escalates_to_double_base() -> None:
    for _ in range(3):
        _fail("@c", _NOW)  # 1st trip @ base
    second = [_fail("@c", _NOW) for _ in range(3)][-1]  # K more failures → 2nd trip

    assert second == _BASE * 2


def test_cooldown_is_capped_at_max() -> None:
    last: float | None = None
    for _ in range(6):  # base, 2x, 4x(=max), max, max, max
        for _ in range(3):
            last = _fail("@c", _NOW)

    assert last == _MAX


def test_reset_zeroes_the_failure_window() -> None:
    # A solved challenge resets the K counter, so sporadic failures spread across many
    # successes never accumulate to K and park a mostly-working channel.
    assert _fail("@c", _NOW) is None  # 1 failure
    _state.reset_challenge_failures("@c")
    assert _fail("@c", _NOW) is None  # counter restarted at 1, not 2
    _state.reset_challenge_failures("@c")
    assert _fail("@c", _NOW) is None  # still 1, not 3
    assert _state.is_channel_in_challenge_backoff("@c", _NOW) is False


def test_k_consecutive_failures_still_trip_after_reset() -> None:
    # A genuine run of K consecutive failures (no interleaved success) still trips.
    _state.reset_challenge_failures("@c")  # reset on a clean channel is a no-op
    _fail("@c", _NOW)
    _fail("@c", _NOW)
    assert _fail("@c", _NOW) == _BASE
    assert _state.is_channel_in_challenge_backoff("@c", _NOW) is True


def test_self_healing_when_cooldown_expires() -> None:
    for _ in range(3):
        _fail("@c", _NOW)
    assert _state.is_channel_in_challenge_backoff("@c", _NOW) is True

    after = _NOW + timedelta(seconds=_BASE + 1)
    assert _state.is_channel_in_challenge_backoff("@c", after) is False


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
