"""Tests for ``services.neurocomment.engine`` — the on-post comment pipeline.

Telegram (``execute``), Gemini (``generate_text``), the spam probe
(``refresh_spam_status``) and randomness (``rng``) are patched at the service
seam; the account-health reads (``fetch_account`` / trust / readiness) and the
inter-post delay (``asyncio.sleep``) are patched on the engine module. Nothing
hits the network and nothing actually waits. Mirrors
``tests/services/test_neurocomment_onboarding.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    assign_account_to_campaign,
    claim_comment,
    configure_database,
    create_account,
    create_campaign,
    fetch_comment,
    fetch_readiness,
    link_channel_to_campaign,
    mark_comment_posted,
    upsert_readiness,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate
from schemas.gemini import GeminiResult
from schemas.neurocomment import CampaignCreate
from schemas.spam_status import SpamStatusVerdict
from schemas.telegram_actions import ActionResult, NewPostEvent
from schemas.trust import TrustScore
from services.content import try_reserve_sent
from services.neurocomment import _seams, _state, engine

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator
    from pathlib import Path

    from schemas.telegram_actions import ActionStatus, TelegramAction


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    # GeminiRequest requires a non-empty key; a real deployment sets GEMINI__API_KEY.
    monkeypatch.setattr(settings.gemini, "api_key", "test-key")
    reset_logging_for_tests()
    setup_logging()
    _state.reset_for_tests()
    # Generation/post never actually wait.
    monkeypatch.setattr(engine.asyncio, "sleep", _no_sleep)
    # Default health: account is healthy, trust good, spam clean, readiness ready.
    monkeypatch.setattr(engine, "evaluate_readiness", lambda *_a, **_k: _Readiness(ready=True))
    monkeypatch.setattr(
        engine,
        "account_trust_score",
        _async_return(TrustScore(account_id="x", score=90, band="good")),
    )
    monkeypatch.setattr(
        _seams,
        "refresh_spam_status",
        _async_return(
            SpamStatusVerdict(account_id="x", status="clean", checked_at="2026-01-01T00:00:00"),
        ),
    )
    yield
    _state.reset_for_tests()


class _Readiness:
    def __init__(self, *, ready: bool, reasons: list[str] | None = None) -> None:
        self.ready = ready
        self.reasons = reasons or []


async def _no_sleep(_seconds: float) -> None:
    return None


def _async_return(value: object) -> Callable[..., Awaitable[object]]:
    async def _fn(*_a: object, **_k: object) -> object:
        return value

    return _fn


class _CommentStub:
    """Captures ``CommentOnPost`` calls and returns a canned ``ActionResult``."""

    def __init__(
        self,
        *,
        status: ActionStatus = "ok",
        message_id: int | None = 555,
        error_type: str | None = None,
        flood_wait_seconds: int | None = None,
    ) -> None:
        self.status = status
        self.message_id = message_id
        self.error_type = error_type
        self.flood_wait_seconds = flood_wait_seconds
        self.calls: list[tuple[str, TelegramAction]] = []

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        self.calls.append((account_id, action))
        return ActionResult(
            status=self.status,
            action_type=action.action_type,
            account_id=account_id,
            message_id=self.message_id if self.status == "ok" else None,
            error_type=self.error_type,
            flood_wait_seconds=self.flood_wait_seconds,
        )


class _GenStub:
    """Returns a sequence of canned generated texts (cycles the last one)."""

    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.calls = 0

    async def generate_text(self, _request: object) -> GeminiResult:
        text = self.texts[min(self.calls, len(self.texts) - 1)]
        self.calls += 1
        return GeminiResult(status="ok", text=text)


async def _make_campaign(channel: str, *accounts: str) -> str:
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="mention X"))
    await link_channel_to_campaign(campaign.campaign_id, channel)
    for acc in accounts:
        await create_account(AccountCreate(account_id=acc, label=acc, session_name=acc))
        await assign_account_to_campaign(campaign.campaign_id, acc)
        await upsert_readiness(acc, channel, joined=True, captcha_passed=True, ready=True)
    return campaign.campaign_id


def _patch_io(
    monkeypatch: pytest.MonkeyPatch,
    *,
    comment: _CommentStub,
    gen: _GenStub | None = None,
) -> None:
    monkeypatch.setattr(_seams, "execute", comment.execute)
    monkeypatch.setattr(_seams, "rng", _FixedRng())
    monkeypatch.setattr(_seams, "generate_text", (gen or _GenStub("a nice comment")).generate_text)


class _FixedRng:
    """Deterministic rng: ``choice`` picks the first item, ``uniform`` the low bound."""

    @staticmethod
    def choice(seq: list[str]) -> str:
        return seq[0]

    @staticmethod
    def uniform(low: float, _high: float) -> float:
        return low


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_happy_path_posts_and_marks_posted(monkeypatch: pytest.MonkeyPatch) -> None:
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="ok", message_id=999)
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hello world"))

    assert [a.action_type for _, a in comment.calls] == ["comment_on_post"]
    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "posted"
    assert record.comment_msg_id == 999
    assert record.comment_text == "a nice comment"


@pytest.mark.asyncio
async def test_posted_with_unknown_msg_id_stores_none(monkeypatch: pytest.MonkeyPatch) -> None:
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="ok", message_id=None)
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hello world"))

    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "posted"
    # No real id from Telegram → NULL, not an ambiguous 0 sentinel.
    assert record.comment_msg_id is None


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_already_claimed_post_does_not_generate_or_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = await _make_campaign("@chan", "acc-1", "acc-other")
    # Another worker already claimed this post.
    assert await claim_comment("@chan", 10, campaign_id, "acc-other") is True
    comment = _CommentStub()
    gen = _GenStub("should not be generated")
    _patch_io(monkeypatch, comment=comment, gen=gen)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert comment.calls == []
    assert gen.calls == 0


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event",
    [
        NewPostEvent(channel="@chan", post_id=1, text="real text", is_forward=True),
        NewPostEvent(channel="@chan", post_id=2, text="   ", has_media=True),
        NewPostEvent(channel="@chan", post_id=3, text="", has_media=False),
        NewPostEvent(channel="@chan", post_id=4, text="https://t.me/spam"),
    ],
)
async def test_filtered_events_never_claim_or_post(
    monkeypatch: pytest.MonkeyPatch,
    event: NewPostEvent,
) -> None:
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(event)

    assert comment.calls == []
    assert await fetch_comment("@chan", event.post_id) is None


@pytest.mark.asyncio
async def test_no_active_campaign_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@unwatched", post_id=1, text="hi"))

    assert comment.calls == []
    assert await fetch_comment("@unwatched", 1) is None


# --------------------------------------------------------------------------- #
# Account selection gates
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_not_ready_account_is_skipped_no_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = await _make_campaign("@chan", "acc-1")
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=False, ready=False)
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert comment.calls == []
    assert await fetch_comment("@chan", 10) is None
    assert campaign_id  # silence unused


@pytest.mark.asyncio
async def test_missing_account_row_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    await _make_campaign("@chan", "acc-1")
    monkeypatch.setattr(engine, "fetch_account", _async_return(None))
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert comment.calls == []
    assert await fetch_comment("@chan", 10) is None


@pytest.mark.asyncio
async def test_gemini_error_regenerates_then_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    await _make_campaign("@chan", "acc-1")
    monkeypatch.setattr(settings.neurocomment, "max_retries", 1)

    class _ErrGen:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_text(self, _request: object) -> GeminiResult:
            self.calls += 1
            return GeminiResult(status="error", error="boom")

    gen = _ErrGen()
    comment = _CommentStub()
    monkeypatch.setattr(_seams, "execute", comment.execute)
    monkeypatch.setattr(_seams, "rng", _FixedRng())
    monkeypatch.setattr(_seams, "generate_text", gen.generate_text)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert gen.calls == 2  # tried + one retry, both errored
    assert comment.calls == []
    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "failed"


@pytest.mark.asyncio
async def test_unhealthy_account_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    await _make_campaign("@chan", "acc-1")
    monkeypatch.setattr(engine, "evaluate_readiness", lambda *_a, **_k: _Readiness(ready=False))
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert comment.calls == []
    assert await fetch_comment("@chan", 10) is None


@pytest.mark.asyncio
async def test_over_hourly_cap_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = await _make_campaign("@chan", "acc-1")
    monkeypatch.setattr(settings.neurocomment, "max_comments_per_hour", 1)
    # One already posted this hour → at the cap.
    assert await claim_comment("@chan", 1, campaign_id, "acc-1") is True
    await mark_comment_posted("@chan", 1, comment_text="x", comment_msg_id=1)
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hi"))

    assert comment.calls == []
    assert await fetch_comment("@chan", 2) is None


@pytest.mark.asyncio
async def test_over_channel_day_cap_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = await _make_campaign("@chan", "acc-1")
    monkeypatch.setattr(settings.neurocomment, "max_comments_per_channel_per_day", 1)
    monkeypatch.setattr(settings.neurocomment, "max_comments_per_hour", 100)
    assert await claim_comment("@chan", 1, campaign_id, "acc-1") is True
    await mark_comment_posted("@chan", 1, comment_text="x", comment_msg_id=1)
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hi"))

    assert comment.calls == []


@pytest.mark.asyncio
async def test_channel_day_cap_zero_disables_it(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = await _make_campaign("@chan", "acc-1")
    monkeypatch.setattr(settings.neurocomment, "max_comments_per_channel_per_day", 0)
    assert await claim_comment("@chan", 1, campaign_id, "acc-1") is True
    await mark_comment_posted("@chan", 1, comment_text="x", comment_msg_id=1)
    comment = _CommentStub(status="ok", message_id=2)
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hi"))

    # Cap disabled → second comment still posts.
    assert len(comment.calls) == 1


@pytest.mark.asyncio
async def test_no_available_account_skips_with_no_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    # Campaign with a channel but no assigned accounts.
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert comment.calls == []
    assert await fetch_comment("@chan", 10) is None


def test_set_cooldown_keeps_the_later_expiry() -> None:
    now = datetime.now(UTC)
    _state.set_cooldown("acc-x", now + timedelta(hours=2))
    # A shorter cooldown must not shorten an existing longer one.
    _state.set_cooldown("acc-x", now + timedelta(minutes=1))
    assert _state.in_cooldown("acc-x", now + timedelta(hours=1)) is True


def test_channel_cooldown_does_not_block_other_channels() -> None:
    now = datetime.now(UTC)
    _state.set_cooldown("acc-x", now + timedelta(hours=1), channel="@a")
    assert _state.in_cooldown("acc-x", now, "@a") is True
    assert _state.in_cooldown("acc-x", now, "@b") is False
    # An account-wide cooldown (channel=None) blocks every channel.
    _state.set_cooldown("acc-x", now + timedelta(hours=1))
    assert _state.in_cooldown("acc-x", now, "@b") is True


def test_in_cooldown_evicts_expired_keys() -> None:
    now = datetime.now(UTC)
    _state.set_cooldown("acc-x", now - timedelta(seconds=1), channel="@a")
    assert _state.in_cooldown("acc-x", now, "@a") is False
    # The expired key is dropped, not left to accumulate.
    assert ("acc-x", "@a") not in _state._COOLDOWN_UNTIL


def test_channel_backoff_escalates_and_caps() -> None:
    now = datetime.now(UTC)
    durations = [
        _state.trip_channel_backoff("@a", now, base_seconds=100.0, max_seconds=1000.0)
        for _ in range(6)
    ]
    # base, then doubling each consecutive trip, capped at max and pinned there after.
    assert durations == [100.0, 200.0, 400.0, 800.0, 1000.0, 1000.0]
    assert _state.channel_in_backoff("@a", now) is True


def test_channel_backoff_first_trip_respects_cap() -> None:
    now = datetime.now(UTC)
    # A misconfigured base > max must still be capped on the very first trip.
    seconds = _state.trip_channel_backoff("@a", now, base_seconds=5000.0, max_seconds=1000.0)
    assert seconds == 1000.0


def test_channel_backoff_is_per_channel() -> None:
    now = datetime.now(UTC)
    _state.trip_channel_backoff("@a", now, base_seconds=3600.0, max_seconds=7200.0)
    assert _state.channel_in_backoff("@a", now) is True
    assert _state.channel_in_backoff("@b", now) is False


def test_channel_backoff_evicts_expired() -> None:
    now = datetime.now(UTC)
    _state.trip_channel_backoff(
        "@a", now - timedelta(hours=2), base_seconds=3600.0, max_seconds=7200.0
    )
    # The 1h cooldown set 2h ago has expired → not cooled, key evicted.
    assert _state.channel_in_backoff("@a", now) is False
    assert "@a" not in _state._CHANNEL_COOLDOWN_UNTIL


@pytest.mark.asyncio
async def test_account_in_cooldown_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    await _make_campaign("@chan", "acc-1")
    _state.set_cooldown("acc-1", datetime.now(UTC) + timedelta(hours=1))
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert comment.calls == []


@pytest.mark.asyncio
async def test_channel_in_backoff_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    await _make_campaign("@chan", "acc-1")
    nc = settings.neurocomment
    _state.trip_channel_backoff(
        "@chan",
        datetime.now(UTC),
        base_seconds=nc.channel_backoff_base_seconds,
        max_seconds=nc.channel_backoff_max_seconds,
    )
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    # Skipped before account selection/claim: no comment sent, no claim row created.
    assert comment.calls == []
    assert await fetch_comment("@chan", 10) is None


# --------------------------------------------------------------------------- #
# Light check + regeneration
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_duplicate_then_unique_regenerates_and_posts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _make_campaign("@chan", "acc-1")
    # First text is a duplicate (reserve it up front), second is fresh.
    assert await try_reserve_sent("dup text") is True
    gen = _GenStub("dup text", "fresh text")
    comment = _CommentStub(status="ok", message_id=7)
    _patch_io(monkeypatch, comment=comment, gen=gen)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert gen.calls == 2  # regenerated past the duplicate
    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "posted"
    assert record.comment_text == "fresh text"


@pytest.mark.asyncio
async def test_exhausting_retries_marks_failed_and_does_not_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _make_campaign("@chan", "acc-1")
    monkeypatch.setattr(settings.neurocomment, "max_retries", 1)
    monkeypatch.setattr(settings.neurocomment, "comment_max_words", 3)
    # Every generation exceeds the word cap → always rejected.
    gen = _GenStub("one two three four five six seven")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment, gen=gen)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert comment.calls == []  # never posted
    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "failed"


@pytest.mark.asyncio
async def test_link_in_generated_text_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    await _make_campaign("@chan", "acc-1")
    monkeypatch.setattr(settings.neurocomment, "max_retries", 0)
    gen = _GenStub("buy now https://t.me/x")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment, gen=gen)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert comment.calls == []
    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "failed"


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
