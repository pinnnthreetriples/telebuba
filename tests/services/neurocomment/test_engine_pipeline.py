"""Tests for neurocomment engine pipeline behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import (
    assign_account_to_campaign,
    claim_comment,
    create_account,
    create_campaign,
    fetch_comment,
    link_channel_to_campaign,
    list_recent_logs,
    mark_comment_posted,
    upsert_readiness,
)
from core.repositories.neurocomment import (
    set_campaign_account_channels,
    set_campaign_status,
)
from schemas.accounts import AccountCreate, AccountList
from schemas.gemini import GeminiResult
from schemas.neurocomment import CampaignCreate
from schemas.telegram_actions import NewPostEvent
from services.neurocomment import _seams, _state, engine
from tests.services.neurocomment.engine_support import (
    _async_return,
    _CommentStub,
    _FixedRng,
    _GenStub,
    _make_campaign,
    _patch_io,
    _Readiness,
)

pytestmark = pytest.mark.usefixtures("isolate_engine")

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
async def test_pinned_account_not_selected_for_other_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An account pinned to @a is never selected for a post in @b, even though ready there."""
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="mention X"))
    for channel in ("@a", "@b"):
        await link_channel_to_campaign(campaign.campaign_id, channel)
    await create_account(AccountCreate(account_id="acc-1", label="a", session_name="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    # Ready on BOTH channels, but pinned to @a.
    await upsert_readiness("acc-1", "@a", joined=True, captcha_passed=True, ready=True)
    await upsert_readiness("acc-1", "@b", joined=True, captcha_passed=True, ready=True)
    await set_campaign_account_channels(campaign.campaign_id, "acc-1", ["@a"])

    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)

    # Post in @b → the only account is pinned to @a → no selection, no comment.
    await engine.handle_new_post(NewPostEvent(channel="@b", post_id=1, text="hello world"))
    assert comment.calls == []
    assert await fetch_comment("@b", 1) is None

    # Post in @a → the pinned account IS eligible and comments.
    await engine.handle_new_post(NewPostEvent(channel="@a", post_id=2, text="hello world"))
    assert [a.action_type for _, a in comment.calls] == ["comment_on_post"]


@pytest.mark.asyncio
async def test_unpinned_account_eligible_for_every_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unpinned account (channel NULL) still comments on any campaign channel."""
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="mention X"))
    for channel in ("@a", "@b"):
        await link_channel_to_campaign(campaign.campaign_id, channel)
    await create_account(AccountCreate(account_id="acc-1", label="a", session_name="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")  # no pin
    await upsert_readiness("acc-1", "@b", joined=True, captcha_passed=True, ready=True)

    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@b", post_id=1, text="hello world"))
    assert [a.action_type for _, a in comment.calls] == ["comment_on_post"]


@pytest.mark.asyncio
async def test_no_active_campaign_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@unwatched", post_id=1, text="hi"))

    assert comment.calls == []
    assert await fetch_comment("@unwatched", 1) is None


@pytest.mark.asyncio
async def test_paused_campaign_posts_are_not_commented(monkeypatch: pytest.MonkeyPatch) -> None:
    """A paused campaign's posts are skipped; flipping back to active resumes commenting (#6)."""
    campaign_id = await _make_campaign("@chan", "acc-1")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)

    # Paused → the engine cannot resolve an active campaign for the channel → no-op.
    await set_campaign_status(campaign_id, "paused")
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=1, text="hello world"))
    assert comment.calls == []
    assert await fetch_comment("@chan", 1) is None

    # Active again → commenting resumes.
    await set_campaign_status(campaign_id, "active")
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hello world"))
    assert [a.action_type for _, a in comment.calls] == ["comment_on_post"]
    record = await fetch_comment("@chan", 2)
    assert record is not None
    assert record.status == "posted"


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


# --------------------------------------------------------------------------- #
# Miss-reason logging — the activity log must say *why* nothing happened.
# --------------------------------------------------------------------------- #


async def _latest_reason(event: str) -> object | None:
    for entry in await list_recent_logs(limit=50):
        if entry.event == event:
            return entry.extra.get("reason")
    return None


@pytest.mark.asyncio
async def test_no_account_reason_quota_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hourly per-account cap full → ``quota_hour`` names the specific limit."""
    campaign_id = await _make_campaign("@chan", "acc-1")
    monkeypatch.setattr(settings.neurocomment, "max_comments_per_hour", 1)
    assert await claim_comment("@chan", 1, campaign_id, "acc-1") is True
    await mark_comment_posted("@chan", 1, comment_text="x", comment_msg_id=1)
    _patch_io(monkeypatch, comment=_CommentStub())

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hi"))

    assert await _latest_reason("neurocomment_no_account_available") == "quota_hour"


@pytest.mark.asyncio
async def test_no_account_reason_quota_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under the hourly cap but the per-channel daily cap is full → ``quota_day``."""
    campaign_id = await _make_campaign("@chan", "acc-1")
    monkeypatch.setattr(settings.neurocomment, "max_comments_per_hour", 100)
    monkeypatch.setattr(settings.neurocomment, "max_comments_per_channel_per_day", 1)
    assert await claim_comment("@chan", 1, campaign_id, "acc-1") is True
    await mark_comment_posted("@chan", 1, comment_text="x", comment_msg_id=1)
    _patch_io(monkeypatch, comment=_CommentStub())

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hi"))

    assert await _latest_reason("neurocomment_no_account_available") == "quota_day"


@pytest.mark.asyncio
async def test_no_account_reason_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    await _make_campaign("@chan", "acc-1")
    _state.set_cooldown("acc-1", datetime.now(UTC) + timedelta(hours=1))
    _patch_io(monkeypatch, comment=_CommentStub())

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert await _latest_reason("neurocomment_no_account_available") == "cooldown"


@pytest.mark.asyncio
async def test_no_account_reason_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    await _make_campaign("@chan", "acc-1")
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=False, ready=False)
    _patch_io(monkeypatch, comment=_CommentStub())

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert await _latest_reason("neurocomment_no_account_available") == "not_ready"


@pytest.mark.asyncio
async def test_no_account_reason_no_accounts_linked(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    _patch_io(monkeypatch, comment=_CommentStub())

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert await _latest_reason("neurocomment_no_account_available") == "no_accounts_linked"


@pytest.mark.asyncio
async def test_generation_exhausted_reason_gemini_error(monkeypatch: pytest.MonkeyPatch) -> None:
    await _make_campaign("@chan", "acc-1")
    monkeypatch.setattr(settings.neurocomment, "max_retries", 0)
    monkeypatch.setattr(_seams, "execute", _CommentStub().execute)
    monkeypatch.setattr(_seams, "rng", _FixedRng())
    monkeypatch.setattr(
        _seams, "generate_text", _async_return(GeminiResult(status="error", error="boom"))
    )

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert await _latest_reason("neurocomment_generation_exhausted") == "gemini_error"


@pytest.mark.asyncio
async def test_generation_exhausted_reason_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    await _make_campaign("@chan", "acc-1")
    monkeypatch.setattr(settings.neurocomment, "max_retries", 0)
    monkeypatch.setattr(_seams, "execute", _CommentStub().execute)
    monkeypatch.setattr(_seams, "rng", _FixedRng())
    monkeypatch.setattr(
        _seams, "generate_text", _async_return(GeminiResult(status="rate_limited", error="429"))
    )

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert await _latest_reason("neurocomment_generation_exhausted") == "gemini_rate_limited"


@pytest.mark.asyncio
async def test_generation_exhausted_reason_too_long(monkeypatch: pytest.MonkeyPatch) -> None:
    await _make_campaign("@chan", "acc-1")
    monkeypatch.setattr(settings.neurocomment, "max_retries", 0)
    monkeypatch.setattr(settings.neurocomment, "comment_max_words", 2)
    monkeypatch.setattr(_seams, "execute", _CommentStub().execute)
    monkeypatch.setattr(_seams, "rng", _FixedRng())
    monkeypatch.setattr(
        _seams, "generate_text", _async_return(GeminiResult(status="ok", text="one two three four"))
    )

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert await _latest_reason("neurocomment_generation_exhausted") == "too_long"


@pytest.mark.asyncio
async def test_missing_account_row_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    await _make_campaign("@chan", "acc-1")
    # Account is assigned + ready, but absent from the bulk account read → skipped.
    monkeypatch.setattr(engine, "list_accounts", _async_return(AccountList(accounts=[])))
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
