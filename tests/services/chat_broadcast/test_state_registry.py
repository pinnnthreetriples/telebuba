"""The run's in-memory registries: rests by chat, the scheduler's tick, halts and retries."""

from __future__ import annotations

import pytest

from services.chat_broadcast import _state


def test_a_rest_is_remembered_per_chat_and_forgotten_with_the_campaign() -> None:
    _state.note_rest_started("c1", "alpha", 10)
    _state.note_rest_started("c1", "beta", 20)
    _state.note_rest_started("c2", "alpha", 30)

    assert _state.rest_started("c1", "alpha") == 10
    assert _state.rest_starts("c1") == {"alpha": 10, "beta": 20}
    _state.forget_rest("c1", "alpha")
    assert _state.rest_started("c1", "alpha") is None

    _state.forget_campaign("c1")

    assert _state.rest_starts("c1") == {}
    assert _state.rest_started("c2", "alpha") == 30


def test_the_tick_lock_is_one_per_campaign_and_its_time_is_remembered() -> None:
    assert _state.tick_lock("c1") is _state.tick_lock("c1")
    assert _state.tick_lock("c1") is not _state.tick_lock("c2")
    assert _state.last_tick("c1") is None

    _state.note_tick("c1", 5)

    assert _state.last_tick("c1") == 5
    _state.forget_campaign("c1")
    assert _state.last_tick("c1") is None


def test_retries_count_per_account_and_reset_by_account_or_campaign() -> None:
    _state.note_retry("c1", "a1")
    _state.note_retry("c1", "a1")
    _state.note_retry("c1", "a2")
    _state.note_retry("c2", "a1")

    assert (_state.retries("c1", "a1"), _state.retries("c1", "a2")) == (2, 1)
    _state.reset_retries("c1", "a1")
    assert (_state.retries("c1", "a1"), _state.retries("c1", "a2")) == (0, 1)
    _state.reset_retries("c1")
    assert (_state.retries("c1", "a2"), _state.retries("c2", "a1")) == (0, 1)


def test_the_time_of_a_halt_goes_with_the_campaign() -> None:
    assert _state.halted_at("c1", "a1") is None
    _state.note_halt("c1", "a1", 7)

    assert _state.halted_at("c1", "a1") == 7
    _state.forget_campaign("c1")
    assert _state.halted_at("c1", "a1") is None


@pytest.mark.asyncio
async def test_forgetting_a_campaign_keeps_the_tick_lock_a_pass_still_holds() -> None:
    lock = _state.tick_lock("c1")
    async with lock:
        _state.forget_campaign("c1")
        # A pass is running: a second lock would let the next one in beside it.
        assert _state.tick_lock("c1") is lock
    _state.forget_campaign("c1")
    assert _state.tick_lock("c1") is not lock
