"""A channel that will not let us write gets 4 rounds over 4 days, then leaves (#147).

K consecutive write failures end a round: the channel is paused for a flat
``channel_pause_hours`` and its round counter goes up. Counter and deadline live on the
campaign link, NOT in memory — the live app restarted 7 times in three days, and a
four-day rule built on module dicts never reached round 4. The final round unlinks the
channel instead of pausing it again; a delivered comment clears both.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from core.config import settings
from core.db import (
    _get_engine,
    fetch_active_campaign_for_channel,
    fetch_channel_paused_until,
    fetch_comment,
    list_campaign_channels,
    list_recent_logs,
    upsert_readiness,
)
from core.repositories.neurocomment._tables import _neurocomment_campaign_channels
from schemas.telegram_actions import NewPostEvent
from services.neurocomment import _state, engine
from tests.services.neurocomment.engine_support import (
    _CommentStub,
    _make_campaign,
    _patch_io,
)

pytestmark = pytest.mark.usefixtures("isolate_engine")

_GATE = "ChatWriteForbiddenError"


def _one_failure_per_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """K=1 so one gated post ends a round — the K counter itself is unit-tested."""
    monkeypatch.setattr(settings.neurocomment, "channel_challenge_backoff_min_failures", 1)


async def _rounds(campaign_id: str) -> int:
    links = (await list_campaign_channels(campaign_id)).links
    return links[0].pause_rounds if links else 0


async def _rewind_the_pause() -> None:
    """Move the pause deadline into the past, standing in for the flat window elapsing."""

    def _write() -> None:
        with _get_engine().begin() as connection:
            connection.execute(
                update(_neurocomment_campaign_channels).values(
                    paused_until=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                ),
            )

    await asyncio.to_thread(_write)


async def _gate_a_post(monkeypatch: pytest.MonkeyPatch, post_id: int) -> None:
    """Drive one full round: re-arm the pair, hit the gate, then let the pause elapse.

    Readiness is restored first because the gate parks the pair; in production the next
    onboarding pass does that. Rewinding the deadline afterwards is what lets a test walk
    four rounds without waiting four days.
    """
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    _patch_io(monkeypatch, comment=_CommentStub(status="failed", error_type=_GATE))
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=post_id, text="hi"))
    await _rewind_the_pause()


async def _logged(event: str) -> bool:
    return any(entry.event == event for entry in await list_recent_logs(limit=100))


@pytest.mark.asyncio
async def test_k_failures_pause_the_channel_for_the_flat_window_and_bump_the_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _one_failure_per_round(monkeypatch)
    monkeypatch.setattr(settings.neurocomment, "channel_pause_hours", 24.0)
    campaign_id = await _make_campaign("@chan", "acc-1")
    _patch_io(monkeypatch, comment=_CommentStub(status="failed", error_type=_GATE))
    before = datetime.now(UTC)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=1, text="hi"))

    assert await _rounds(campaign_id) == 1
    until = await fetch_channel_paused_until("@chan")
    assert until is not None
    # A flat 24h, not the 1h first step of the doubling ladder this replaced. The
    # deadline is stamped a beat after ``before``, hence the minute of slack each way.
    window = datetime.fromisoformat(until) - before
    assert timedelta(hours=23, minutes=59) < window < timedelta(hours=24, minutes=1)


@pytest.mark.asyncio
async def test_a_pause_survives_a_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of persisting it: in-memory state never reached round 4."""
    _one_failure_per_round(monkeypatch)
    campaign_id = await _make_campaign("@chan", "acc-1")
    _patch_io(monkeypatch, comment=_CommentStub(status="failed", error_type=_GATE))
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=1, text="hi"))

    # The restart: every module dict the old back-off lived in is cleared.
    _state.reset_for_tests()

    assert await fetch_channel_paused_until("@chan") is not None
    assert await _rounds(campaign_id) == 1
    # ...and the state rebuilt from the DB still blocks the next post.
    comment = _CommentStub(status="ok")
    _patch_io(monkeypatch, comment=comment)
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hello world"))
    assert comment.calls == []


@pytest.mark.asyncio
async def test_a_delivered_comment_clears_both_the_window_and_the_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _one_failure_per_round(monkeypatch)
    campaign_id = await _make_campaign("@chan", "acc-1")
    await _gate_a_post(monkeypatch, post_id=1)
    assert await _rounds(campaign_id) == 1

    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    _patch_io(monkeypatch, comment=_CommentStub(status="ok"))
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hello world"))

    # The channel demonstrably works: its next bad day starts from round 0, unpaused.
    assert await _rounds(campaign_id) == 0
    assert await fetch_channel_paused_until("@chan") is None
    assert _state.register_write_failure("@chan", min_failures=2) is False


@pytest.mark.asyncio
async def test_the_final_round_drops_the_channel_instead_of_pausing_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _one_failure_per_round(monkeypatch)
    monkeypatch.setattr(settings.neurocomment, "channel_max_rounds", 4)
    await _make_campaign("@chan", "acc-1")

    for post_id in (1, 2, 3):  # rounds 1-3 only pause
        await _gate_a_post(monkeypatch, post_id=post_id)
        assert await fetch_active_campaign_for_channel("@chan") is not None

    await _gate_a_post(monkeypatch, post_id=4)  # round 4 is the last one

    # The active link is gone, so the listener reconciles and stops watching the channel.
    assert await fetch_active_campaign_for_channel("@chan") is None
    dropped = next(
        entry
        for entry in await list_recent_logs(limit=100)
        if entry.event == "neurocomment_channel_dropped"
    )
    assert dropped.extra["channel"] == "@chan"
    assert dropped.extra["rounds"] == 4
    assert dropped.extra["reason"] == "write_blocked"
    # No account leaves the chat: this is the channel forbidding comments, not a personal
    # ban, and re-joining later would spend the rolling-24h join cap for nothing.
    assert await _logged("neurocomment_account_banned") is False


@pytest.mark.asyncio
async def test_a_paused_channel_blocks_posting(monkeypatch: pytest.MonkeyPatch) -> None:
    _one_failure_per_round(monkeypatch)
    await _make_campaign("@chan", "acc-1")
    _patch_io(monkeypatch, comment=_CommentStub(status="failed", error_type=_GATE))
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=1, text="hi"))

    comment = _CommentStub(status="ok")
    _patch_io(monkeypatch, comment=comment)
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hello world"))

    assert comment.calls == []
    assert await fetch_comment("@chan", 2) is None


@pytest.mark.asyncio
async def test_an_expired_pause_lets_the_next_post_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing un-pauses a channel: the deadline simply passes and the next post tries."""
    _one_failure_per_round(monkeypatch)
    await _make_campaign("@chan", "acc-1")
    await _gate_a_post(monkeypatch, post_id=1)

    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    comment = _CommentStub(status="ok")
    _patch_io(monkeypatch, comment=comment)
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hello world"))

    assert len(comment.posts) == 1


@pytest.mark.asyncio
async def test_the_deletion_sweep_backoff_is_untouched() -> None:
    """Only the challenge/gate mechanism changed; the deletion back-off still escalates."""
    now = datetime.now(UTC)
    await _make_campaign("@chan", "acc-1")

    first = _state.trip_channel_backoff("@chan", now, base_seconds=100.0, max_seconds=400.0)
    second = _state.trip_channel_backoff("@chan", now, base_seconds=100.0, max_seconds=400.0)

    assert (first, second) == (100.0, 200.0)  # still doubling, still in memory
    assert _state.channel_in_backoff("@chan", now) is True
    assert await fetch_channel_paused_until("@chan") is None  # a different park entirely
