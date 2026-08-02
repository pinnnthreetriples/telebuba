"""Tests for neurocomment engine outcomes behavior."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    _get_engine,
    bump_channel_pause,
    fetch_channel_paused_until,
    fetch_comment,
    fetch_readiness,
    insert_challenge,
    link_channel_to_campaign,
    list_failed_for_channel,
    list_recent_logs,
    mark_human_skipped,
    upsert_readiness,
)
from schemas.accounts import AccountRead
from schemas.challenge import ChallengeDecision, ChallengeInsert
from schemas.neurocomment import NeurocommentSettings
from schemas.telegram_actions import NewPostEvent
from services.content import try_reserve_sent
from services.neurocomment import _outcomes, _seams, _state, engine
from tests.services.neurocomment.engine_support import (
    _CommentStub,
    _FixedRng,
    _make_campaign,
    _patch_ban_confirmation,
    _patch_io,
)

if TYPE_CHECKING:
    from schemas.gemini import GeminiResult

pytestmark = pytest.mark.usefixtures("isolate_engine")


async def _has_event(event: str) -> bool:
    return any(entry.event == event for entry in await list_recent_logs(limit=50))


async def _latest_extra(event: str, key: str) -> object | None:
    for entry in await list_recent_logs(limit=50):
        if entry.event == event:
            return entry.extra.get(key)
    return None


# --------------------------------------------------------------------------- #
# Post-time error classification
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_flood_wait_sets_cooldown_marks_failed_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="flood_wait", flood_wait_seconds=300)
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "failed"
    assert _state.in_cooldown("acc-1", datetime.now(UTC)) is True
    # Released: the same text can be reserved again afterwards.
    assert await try_reserve_sent("a nice comment") is True


@pytest.mark.asyncio
async def test_premium_wait_sets_cooldown_marks_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="premium_wait", flood_wait_seconds=300)
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=11, text="hi"))

    record = await fetch_comment("@chan", 11)
    assert record is not None
    assert record.status == "failed"
    # premium_wait is a flood-family wait → account cools down, not a generic fail.
    assert _state.in_cooldown("acc-1", datetime.now(UTC)) is True


@pytest.mark.asyncio
async def test_peer_flood_uses_config_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    await _make_campaign("@chan", "acc-1")
    monkeypatch.setattr(settings.neurocomment, "peer_flood_cooldown_seconds", 7200)
    comment = _CommentStub(status="peer_flood")
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    # Cooldown extends past the (zero-duration) peer_flood using the config default.
    later = datetime.now(UTC) + timedelta(seconds=3600)
    assert _state.in_cooldown("acc-1", later) is True


@pytest.mark.asyncio
async def test_slow_mode_cools_only_that_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = await _make_campaign("@a", "acc-1")
    await link_channel_to_campaign(campaign_id, "@b")
    await upsert_readiness("acc-1", "@b", joined=True, captcha_passed=True, ready=True)
    comment = _CommentStub(status="slow_mode_wait", flood_wait_seconds=300)
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@a", post_id=1, text="hi"))
    # Slow-mode is per-chat: @a is cooled, the account stays usable elsewhere.
    comment.status = "ok"
    comment.message_id = 7
    await engine.handle_new_post(NewPostEvent(channel="@b", post_id=2, text="hi"))

    rec_a = await fetch_comment("@a", 1)
    assert rec_a is not None
    assert rec_a.status == "failed"  # slow-mode → failed + per-channel cooldown
    rec_b = await fetch_comment("@b", 2)
    assert rec_b is not None
    assert rec_b.status == "posted"  # not blocked by @a's channel cooldown


@pytest.mark.asyncio
async def test_write_forbidden_flips_readiness_lazy_captcha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="failed", error_type="ChatGuestSendForbiddenError")
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert readiness.ready is False
    assert readiness.captcha_passed is False
    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "failed"


async def _seed_pending_challenge(account_id: str, channel: str) -> None:
    decision = ChallengeDecision(
        action="click_button", button_index=0, confidence=0.9, reasoning="r"
    )
    await insert_challenge(
        ChallengeInsert(
            challenge_hash="h",
            account_id=account_id,
            channel=channel,
            raw_text="prove human",
            button_labels=["yes"],
            outcome="pending",
            decision_json=decision.model_dump_json(),
        ),
    )


def _challenge_outcome(account_id: str) -> str | None:
    with _get_engine().connect() as connection:
        return connection.exec_driver_sql(
            "SELECT outcome FROM neurocomment_challenges WHERE account_id = ?",
            (account_id,),
        ).scalar()


@pytest.mark.asyncio
async def test_successful_comment_resolves_pending_challenge_to_solved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ф2 #146: the first successful comment confirms the solver's click worked.
    await _make_campaign("@chan", "acc-1")
    await _seed_pending_challenge("acc-1", "@chan")
    _patch_io(monkeypatch, comment=_CommentStub(status="ok"))

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hello world"))

    assert _challenge_outcome("acc-1") == "solved"


@pytest.mark.asyncio
async def test_solved_comment_resets_the_write_failure_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ф2 #147: sporadic write failures interleaved with successes must not accumulate
    # to K. K=2 here: one failure, then a solved comment (resolves pending→solved and
    # resets the window), then one more failure — the counter is back at 1, no round ends.
    monkeypatch.setattr(settings.neurocomment, "channel_challenge_backoff_min_failures", 2)
    await _make_campaign("@chan", "acc-1")

    _state.register_write_failure("@chan", min_failures=2)
    await _seed_pending_challenge("acc-1", "@chan")
    _patch_io(monkeypatch, comment=_CommentStub(status="ok"))
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hello world"))
    assert _challenge_outcome("acc-1") == "solved"

    # Post-reset, a single fresh failure is the 1st in a new window, not the 2nd.
    assert _state.register_write_failure("@chan", min_failures=2) is False


@pytest.mark.asyncio
async def test_gate_error_resolves_pending_challenge_to_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ф2 #146: a gate error on the first comment means the click did not work.
    await _make_campaign("@chan", "acc-1")
    await _seed_pending_challenge("acc-1", "@chan")
    _patch_io(
        monkeypatch, comment=_CommentStub(status="failed", error_type="ChatWriteForbiddenError")
    )

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert _challenge_outcome("acc-1") == "failed"
    failed = await list_failed_for_channel("@chan", limit=10)
    assert [r.outcome for r in failed.rows] == ["failed"]


@pytest.mark.asyncio
async def test_gate_error_with_pending_pauses_the_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ф2 #147: a resolved pending→failed registers a write failure; K=1 → round 1 pause.
    monkeypatch.setattr(settings.neurocomment, "channel_challenge_backoff_min_failures", 1)
    await _make_campaign("@chan", "acc-1")
    await _seed_pending_challenge("acc-1", "@chan")
    _patch_io(
        monkeypatch, comment=_CommentStub(status="failed", error_type="ChatWriteForbiddenError")
    )

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert await fetch_channel_paused_until("@chan") is not None


@pytest.mark.asyncio
async def test_paused_channel_skips_commenting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ф2 #147: a paused channel is left alone — no account selected, no comment.
    await _make_campaign("@chan", "acc-1")
    await bump_channel_pause("@chan", (datetime.now(UTC) + timedelta(hours=24)).isoformat())
    comment = _CommentStub(status="ok")
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hello world"))

    assert comment.calls == []
    assert await fetch_comment("@chan", 10) is None


@pytest.mark.asyncio
async def test_human_skipped_pair_is_not_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ф2 #148: an operator-skipped pair (ready=0) is never selected to comment.
    await _make_campaign("@chan", "acc-1")
    await mark_human_skipped("acc-1", "@chan")
    comment = _CommentStub(status="ok")
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hello world"))

    assert comment.calls == []
    assert await fetch_comment("@chan", 10) is None


@pytest.mark.asyncio
async def test_human_skipped_pair_not_selected_even_if_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Defense-in-depth: even a readiness row that is ready=1 AND human_skipped=1 (e.g. a
    # stale re-enable) must never be selected — the engine honours the operator skip.
    await _make_campaign("@chan", "acc-1")  # seeds ready=1
    await mark_human_skipped("acc-1", "@chan")
    # Re-assert ready=1 to simulate a re-enable that left human_skipped set.
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    comment = _CommentStub(status="ok")
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hello world"))

    assert comment.calls == []
    assert await fetch_comment("@chan", 10) is None


@pytest.mark.asyncio
async def test_generic_post_failure_marks_failed_and_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="failed", error_type="SomeOtherError")
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "failed"
    assert await try_reserve_sent("a nice comment") is True


# --------------------------------------------------------------------------- #
# Error isolation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unexpected_exception_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    await _make_campaign("@chan", "acc-1")

    async def boom(_request: object) -> GeminiResult:
        msg = "generation exploded"
        raise RuntimeError(msg)

    comment = _CommentStub()
    monkeypatch.setattr(_seams, "execute", comment.execute)
    monkeypatch.setattr(_seams, "rng", _FixedRng())
    monkeypatch.setattr(_seams, "generate_text", boom)

    # Must not raise — a pipeline fault cannot be allowed to kill the listener task.
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert comment.calls == []


@pytest.mark.asyncio
async def test_exception_after_claim_marks_failed_not_claimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A raise after the claim was won (here: generation explodes) must leave the row
    # failed, not stuck 'claimed' forever — otherwise the post is never commentable
    # and quota is permanently consumed for the rolling window.
    await _make_campaign("@chan", "acc-1")

    async def boom(_request: object) -> GeminiResult:
        msg = "generation exploded"
        raise RuntimeError(msg)

    comment = _CommentStub()
    monkeypatch.setattr(_seams, "execute", comment.execute)
    monkeypatch.setattr(_seams, "rng", _FixedRng())
    monkeypatch.setattr(_seams, "generate_text", boom)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "failed"  # not 'claimed'


@pytest.mark.asyncio
async def test_cancelled_after_claim_marks_failed_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Shutdown mid-flight (CancelledError after the claim) must clean up the row and
    # re-raise so the task actually cancels — a stuck 'claimed' row would leak.
    await _make_campaign("@chan", "acc-1")

    async def cancelled(_request: object) -> GeminiResult:
        raise asyncio.CancelledError

    comment = _CommentStub()
    monkeypatch.setattr(_seams, "execute", comment.execute)
    monkeypatch.setattr(_seams, "rng", _FixedRng())
    monkeypatch.setattr(_seams, "generate_text", cancelled)

    with pytest.raises(asyncio.CancelledError):
        await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "failed"  # not 'claimed'


@pytest.mark.asyncio
async def test_selection_reads_cached_spam_and_never_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Q2: the post path scores health from the cached spam bulk-read and must never
    # probe @SpamBot — probing per post is itself a ban signal.
    await _make_campaign("@chan", "acc-1")
    probed: list[object] = []

    async def _boom(*args: object, **_kwargs: object) -> object:
        probed.append(args)
        msg = "refresh_spam_status must not be called on the post path"
        raise AssertionError(msg)

    monkeypatch.setattr(_seams, "refresh_spam_status", _boom)
    comment = _CommentStub(status="ok", message_id=5)
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hello world"))

    assert probed == []
    assert len(comment.calls) == 1


def test_min_trust_gate_rejects_below_threshold() -> None:
    """An account whose Trust Score is below the operator's floor is not healthy."""
    account = AccountRead(
        account_id="low",
        status="new",  # not alive → docks trust well below a 100 floor
        created_at="2026-06-30T00:00:00+00:00",
        updated_at="2026-06-30T00:00:00+00:00",
    )
    limits = NeurocommentSettings(
        max_comments_per_hour=10,
        max_comments_per_channel_per_day=3,
        reply_delay_min_seconds=1.0,
        reply_delay_max_seconds=2.0,
        min_trust_score=100,
        updated_at="now",
    )
    pool = engine._SelectionPool(
        accounts={"low": account},
        ready_account_ids=frozenset({"low"}),
        states={},
        spam={},
        fingerprints={},
        hourly_counts={},
        daily_counts={},
        limits=limits,
    )

    assert engine._is_healthy(account, 1, datetime.now(UTC), pool) is False


# --------------------------------------------------------------------------- #
# M2 — Telegram-accepted is the commit point
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_posted_but_mark_posted_raises_is_not_marked_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram accepted but the mark-posted DB write blows up → the row is NOT failed."""
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="ok", message_id=999)
    _patch_io(monkeypatch, comment=comment)

    async def _boom(*_a: object, **_k: object) -> object:
        msg = "db down mid-commit"
        raise RuntimeError(msg)

    monkeypatch.setattr(_outcomes, "mark_comment_posted", _boom)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hello world"))

    assert len(comment.calls) == 1  # the comment WAS delivered
    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status != "failed"  # a delivered comment is never flipped to failed
    assert record.status == "claimed"  # stays claimed (post write failed before the flip)


@pytest.mark.asyncio
async def test_commit_error_after_posted_keeps_posted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure in a post-success follow-up (resolve) leaves the posted row posted."""
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="ok", message_id=999)
    _patch_io(monkeypatch, comment=comment)

    async def _boom(*_a: object, **_k: object) -> bool:
        msg = "resolve down"
        raise RuntimeError(msg)

    monkeypatch.setattr(_outcomes, "resolve_pending_outcome", _boom)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hello world"))

    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "posted"


# --------------------------------------------------------------------------- #
# L2 — only captcha-clearable gates count as a write failure
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_write_forbidden_gate_registers_write_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ChatWriteForbiddenError is a solver-clearable gate → resolves failed + pauses."""
    monkeypatch.setattr(settings.neurocomment, "channel_challenge_backoff_min_failures", 1)
    await _make_campaign("@chan", "acc-1")
    await _seed_pending_challenge("acc-1", "@chan")
    _patch_io(
        monkeypatch, comment=_CommentStub(status="failed", error_type="ChatWriteForbiddenError")
    )

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert await fetch_channel_paused_until("@chan") is not None
    assert _challenge_outcome("acc-1") == "failed"


@pytest.mark.asyncio
async def test_guest_send_forbidden_gate_registers_write_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ChatGuestSendForbiddenError is also solver-clearable → resolves failed + pauses."""
    monkeypatch.setattr(settings.neurocomment, "channel_challenge_backoff_min_failures", 1)
    await _make_campaign("@chan", "acc-1")
    await _seed_pending_challenge("acc-1", "@chan")
    _patch_io(
        monkeypatch,
        comment=_CommentStub(status="failed", error_type="ChatGuestSendForbiddenError"),
    )

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert await fetch_channel_paused_until("@chan") is not None
    assert _challenge_outcome("acc-1") == "failed"


@pytest.mark.asyncio
async def test_user_banned_marks_pair_banned_not_a_solver_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ban sticks the pair banned (ready=0, banned=1); it is NOT a solver failure."""
    monkeypatch.setattr(settings.neurocomment, "channel_challenge_backoff_min_failures", 1)
    await _make_campaign("@chan", "acc-1")
    await _seed_pending_challenge("acc-1", "@chan")
    _patch_io(
        monkeypatch, comment=_CommentStub(status="failed", error_type="UserBannedInChannelError")
    )
    _patch_ban_confirmation(monkeypatch)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    # A ban does not park the channel and does not resolve the pending challenge.
    assert await fetch_channel_paused_until("@chan") is None
    assert _challenge_outcome("acc-1") == "pending"
    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert readiness.ready is False  # a ban flips the pair off for selection
    assert readiness.banned is True  # ...and sticks so a re-onboard can't revive it


@pytest.mark.asyncio
async def test_banned_pair_is_not_selected_for_the_next_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a ban, a fresh post on the same channel finds no eligible account."""
    await _make_campaign("@chan", "acc-1", "acc-2")
    # acc-2 is joined but not ready: it never competes for the post, and its un-banned
    # readiness row keeps the channel linked, so the miss below is the banned pair alone.
    await upsert_readiness("acc-2", "@chan", joined=True, captcha_passed=False, ready=False)
    comment = _CommentStub(status="failed", error_type="UserBannedInChannelError")
    _patch_io(monkeypatch, comment=comment)
    _patch_ban_confirmation(monkeypatch)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=1, text="hi"))
    assert len(comment.posts) == 1  # the ban was hit once

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hi"))

    # No second attempt — the banned pair is excluded from selection.
    assert len(comment.posts) == 1
    assert await _latest_extra("neurocomment_no_account_available", "reason") == "not_ready"


# --------------------------------------------------------------------------- #
# Lost access to the discussion group (ChannelPrivateError)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_channel_private_parks_pair_with_join_failed_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ChannelPrivateError → readiness off via onboarding's hard-join-failure sentinel."""
    monkeypatch.setattr(settings.neurocomment, "channel_challenge_backoff_min_failures", 1)
    await _make_campaign("@chan", "acc-1")
    await _seed_pending_challenge("acc-1", "@chan")
    _patch_io(monkeypatch, comment=_CommentStub(status="failed", error_type="ChannelPrivateError"))

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    # (joined=False, captcha_passed=True) is the sentinel the board renders as join_failed.
    assert (readiness.joined, readiness.captcha_passed, readiness.ready) == (False, True, False)
    assert readiness.banned is False  # not a ban → the next onboarding pass may re-join
    # Not a write failure: the pending challenge is untouched and the channel is unpaused.
    assert _challenge_outcome("acc-1") == "pending"
    assert await fetch_channel_paused_until("@chan") is None
    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "failed"
    assert await try_reserve_sent("a nice comment") is True  # the claim was released
    assert await _has_event("neurocomment_post_access_lost") is True


@pytest.mark.asyncio
async def test_lost_access_pair_is_not_reselected_for_the_next_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression: an unclassified ChannelPrivateError re-picked the pair every post."""
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="failed", error_type="ChannelPrivateError")
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=1, text="hi"))
    assert len(comment.calls) == 1  # access was lost once

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hi"))

    # No second attempt — ready=False excludes the pair instead of looping forever.
    assert len(comment.calls) == 1
    assert await _latest_extra("neurocomment_no_account_available", "reason") == "not_ready"


@pytest.mark.asyncio
async def test_unclassified_failure_parks_the_pair_on_a_bounded_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default branch must touch state too — the loop is a class, not one error name.

    ``core.telegram_client._actions`` funnels every unmapped Telethon exception into one
    generic ``status="failed"``, so any terminal error outside the named families lands
    here. A tail that touched no state re-picked the same pair on the channel's very next
    post forever; #279 closed that for ChannelPrivateError only.
    """
    monkeypatch.setattr(settings.neurocomment, "peer_flood_cooldown_seconds", 3600)
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="failed", error_type="SomeUnmappedRpcError")
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=1, text="hi"))
    assert len(comment.calls) == 1

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hi"))

    assert len(comment.calls) == 1  # parked, not re-attempted on the channel's next post
    assert await _latest_extra("neurocomment_no_account_available", "reason") == "cooldown"
    assert await _has_event("neurocomment_post_failed") is True
    # The feed said "failed" without the cause; the Telegram exception class must ride along.
    assert await _latest_extra("neurocomment_post_failed", "error_type") == "SomeUnmappedRpcError"
    now = datetime.now(UTC)
    assert _state.in_cooldown("acc-1", now, "@chan") is True
    # Bounded and self-expiring: an unknown error is not evidence of lost access, so no
    # readiness write — the pair comes back on its own once the window lapses.
    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert (readiness.ready, readiness.banned) == (True, False)
    assert _state.in_cooldown("acc-1", now + timedelta(seconds=7200), "@chan") is False


# --------------------------------------------------------------------------- #
# A successful post never erases a cooldown parked by a concurrent task
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_successful_post_keeps_a_cooldown_parked_mid_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rival task's fresh flood cooldown survives this task's successful post."""
    await _make_campaign("@chan", "acc-1")
    _patch_io(monkeypatch, comment=_CommentStub(status="ok", message_id=7))

    async def _park_mid_delay(_seconds: float) -> None:
        # Stands in for a rival task hitting FloodWait while this one sleeps in its reply
        # delay — i.e. the account is parked account-wide *after* the selection gate.
        await _state.set_cooldown("acc-1", datetime.now(UTC) + timedelta(seconds=300))

    monkeypatch.setattr(engine.asyncio, "sleep", _park_mid_delay)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hello world"))

    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "posted"  # the comment was delivered (cooldown set post-gate)
    assert _state.in_cooldown("acc-1", datetime.now(UTC)) is True
