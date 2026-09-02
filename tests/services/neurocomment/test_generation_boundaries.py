"""Boundary contracts for comment generation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.config import settings
from schemas.gemini import GeminiResult
from schemas.neurocomment import NeurocommentCampaign
from schemas.telegram_actions import NewPostEvent
from schemas.telegram_actions_comments import PostCommentRecord
from schemas.warming import WarmingSettingsSecret
from services.neurocomment import _generate, _generation_candidates, _seams

pytestmark = pytest.mark.usefixtures("isolate_engine")


def _campaign() -> NeurocommentCampaign:
    return NeurocommentCampaign(
        campaign_id="campaign-1",
        name="Campaign",
        prompt="Be useful",
        status="active",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _secret() -> WarmingSettingsSecret:
    return WarmingSettingsSecret(
        inter_account_chat=False,
        reactions_enabled=True,
        gemini_api_key="key",
        gemini_model="model",
        gemini_max_retries=1,
        gemini_min_interval_seconds=0,
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _event() -> NewPostEvent:
    return NewPostEvent(channel="@channel", post_id=41, text="post")


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (GeminiResult(status="rate_limited", error="429"), "gemini_rate_limited"),
        (GeminiResult(status="ok", text=None), "gemini_empty"),
        (GeminiResult(status="error", error="boom"), "gemini_error"),
    ],
)
def test_gemini_failure_reason_is_stable(result: GeminiResult, reason: str) -> None:
    assert _generate._gemini_reason(result) == reason


@pytest.mark.asyncio
async def test_word_limit_accepts_exact_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.neurocomment, "comment_max_words", 3)
    monkeypatch.setattr(settings.neurocomment, "max_retries", 0)
    monkeypatch.setattr(settings.neurocomment, "semantic_dedup_threshold", 0.0)
    monkeypatch.setattr(
        _generation_candidates, "recent_channel_comments", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        _generation_candidates, "load_warming_settings", AsyncMock(return_value=_secret())
    )
    monkeypatch.setattr(_generation_candidates, "touch_comment_claim", AsyncMock(return_value=True))
    monkeypatch.setattr(
        _seams,
        "generate_text",
        AsyncMock(return_value=GeminiResult(status="ok", text=" one two three ")),
    )

    outcome = await _generate._generate_acceptable(_campaign(), _event(), "account")

    assert outcome.text == "one two three"
    assert outcome.reason is None
    assert outcome.error is None


@pytest.mark.asyncio
async def test_retry_budget_is_initial_attempt_plus_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "comment_max_words", 1)
    monkeypatch.setattr(settings.neurocomment, "max_retries", 2)
    monkeypatch.setattr(
        _generation_candidates, "recent_channel_comments", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        _generation_candidates, "load_warming_settings", AsyncMock(return_value=_secret())
    )
    monkeypatch.setattr(_generation_candidates, "touch_comment_claim", AsyncMock(return_value=True))
    generate = AsyncMock(return_value=GeminiResult(status="ok", text="too many words"))
    monkeypatch.setattr(_seams, "generate_text", generate)

    outcome = await _generate._generate_acceptable(_campaign(), _event(), "account")

    assert outcome.text is None
    assert outcome.reason == "too_long"
    assert outcome.error is None
    assert generate.await_count == 3


@pytest.mark.asyncio
async def test_inflight_reservation_uses_post_generation_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow provider must not consume the reservation's dedup lifetime."""
    started = datetime(2026, 1, 1, tzinfo=UTC)
    finished = started + timedelta(hours=2)
    clock = SimpleNamespace(now=started)

    class ClockDateTime:
        @classmethod
        def now(cls, _tz: object) -> datetime:
            return clock.now

    calls = 0

    async def generate(_request: object) -> GeminiResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            clock.now = finished
        return GeminiResult(status="ok", text="fresh comment")

    monkeypatch.setattr(settings.neurocomment, "max_retries", 0)
    monkeypatch.setattr(settings.neurocomment, "semantic_dedup_threshold", 0.9)
    monkeypatch.setattr(_generation_candidates, "datetime", ClockDateTime)
    monkeypatch.setattr(
        _generation_candidates, "recent_channel_comments", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        _generation_candidates, "load_warming_settings", AsyncMock(return_value=_secret())
    )
    monkeypatch.setattr(_generation_candidates, "touch_comment_claim", AsyncMock(return_value=True))
    monkeypatch.setattr(_generation_candidates, "try_reserve_sent", AsyncMock(return_value=True))
    monkeypatch.setattr(_seams, "generate_text", generate)

    first = await _generate._generate_acceptable(_campaign(), _event(), "account")
    # Still inside the configured 24-hour window measured from provider completion.
    # With the old entry-time timestamp this is 25h old and would be accepted again.
    clock.now = finished + timedelta(hours=23)
    second = await _generate._generate_acceptable(_campaign(), _event(), "account")

    assert first.text == "fresh comment"
    assert first.reason is None
    assert first.error is None
    assert second.text is None
    assert second.reason == "duplicate"
    assert second.error is None


@pytest.mark.asyncio
async def test_semantic_rejection_releases_exact_text_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "max_retries", 0)
    monkeypatch.setattr(settings.neurocomment, "semantic_dedup_threshold", 0.5)
    monkeypatch.setattr(
        _generation_candidates, "recent_channel_comments", AsyncMock(return_value=["alpha beta"])
    )
    monkeypatch.setattr(
        _generation_candidates, "load_warming_settings", AsyncMock(return_value=_secret())
    )
    monkeypatch.setattr(_generation_candidates, "touch_comment_claim", AsyncMock(return_value=True))
    monkeypatch.setattr(
        _seams,
        "generate_text",
        AsyncMock(return_value=GeminiResult(status="ok", text="beta alpha")),
    )
    release = AsyncMock()
    monkeypatch.setattr(_generation_candidates, "release_sent_text", release)

    outcome = await _generate._generate_acceptable(_campaign(), _event(), "account")

    assert outcome.text is None
    assert outcome.reason == "duplicate"
    assert outcome.error is None
    release.assert_awaited_once_with("beta alpha")


@pytest.mark.asyncio
async def test_reply_echoing_the_quoted_comment_is_a_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An answer that parrots the comment it replies to is refused; the next round is not."""
    monkeypatch.setattr(settings.neurocomment, "max_retries", 1)
    monkeypatch.setattr(settings.neurocomment, "semantic_dedup_threshold", 0.5)
    monkeypatch.setattr(
        _generation_candidates, "recent_channel_comments", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        _generation_candidates, "load_warming_settings", AsyncMock(return_value=_secret())
    )
    monkeypatch.setattr(_generation_candidates, "touch_comment_claim", AsyncMock(return_value=True))
    journal = AsyncMock()
    monkeypatch.setattr(_generate, "_log_regeneration", journal)
    generate = AsyncMock(
        side_effect=[
            GeminiResult(status="ok", text="gamma beta alpha"),
            GeminiResult(status="ok", text="a fresh take entirely"),
        ]
    )
    monkeypatch.setattr(_seams, "generate_text", generate)
    target = PostCommentRecord(message_id=7, text="alpha beta gamma")

    outcome = await _generate._generate_acceptable(_campaign(), _event(), "account", target=target)

    assert outcome.text == "a fresh take entirely"
    assert outcome.reason is None
    assert generate.await_count == 2
    journal.assert_awaited_with("account", _event(), 1, "duplicate", None)
