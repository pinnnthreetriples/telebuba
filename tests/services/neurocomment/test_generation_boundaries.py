"""Boundary contracts for comment generation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.config import settings
from schemas.gemini import GeminiResult
from schemas.neurocomment import NeurocommentCampaign
from schemas.warming import WarmingSettingsSecret
from services.neurocomment import _generate, _seams

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
    monkeypatch.setattr(_generate, "_recent_channel_comments", AsyncMock(return_value=[]))
    monkeypatch.setattr(_generate, "load_warming_settings", AsyncMock(return_value=_secret()))
    monkeypatch.setattr(
        _seams,
        "generate_text",
        AsyncMock(return_value=GeminiResult(status="ok", text=" one two three ")),
    )

    outcome = await _generate._generate_acceptable(_campaign(), "@channel", "post")

    assert outcome == ("one two three", None)


@pytest.mark.asyncio
async def test_retry_budget_is_initial_attempt_plus_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "comment_max_words", 1)
    monkeypatch.setattr(settings.neurocomment, "max_retries", 2)
    monkeypatch.setattr(_generate, "_recent_channel_comments", AsyncMock(return_value=[]))
    monkeypatch.setattr(_generate, "load_warming_settings", AsyncMock(return_value=_secret()))
    generate = AsyncMock(return_value=GeminiResult(status="ok", text="too many words"))
    monkeypatch.setattr(_seams, "generate_text", generate)

    outcome = await _generate._generate_acceptable(_campaign(), "@channel", "post")

    assert outcome == (None, "too_long")
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

    async def generate(_request: object) -> GeminiResult:
        clock.now = finished
        return GeminiResult(status="ok", text="fresh comment")

    monkeypatch.setattr(settings.neurocomment, "max_retries", 0)
    monkeypatch.setattr(settings.neurocomment, "semantic_dedup_threshold", 0.9)
    monkeypatch.setattr(_generate, "datetime", ClockDateTime)
    monkeypatch.setattr(_generate, "_recent_channel_comments", AsyncMock(return_value=[]))
    monkeypatch.setattr(_generate, "load_warming_settings", AsyncMock(return_value=_secret()))
    monkeypatch.setattr(_seams, "generate_text", generate)

    outcome = await _generate._generate_acceptable(_campaign(), "@channel", "post")

    assert outcome.text == "fresh comment"
    assert _generate._INFLIGHT["@channel"] == [("fresh comment", finished)]


@pytest.mark.asyncio
async def test_semantic_rejection_releases_exact_text_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "max_retries", 0)
    monkeypatch.setattr(settings.neurocomment, "semantic_dedup_threshold", 0.5)
    monkeypatch.setattr(
        _generate, "_recent_channel_comments", AsyncMock(return_value=["alpha beta"])
    )
    monkeypatch.setattr(_generate, "load_warming_settings", AsyncMock(return_value=_secret()))
    monkeypatch.setattr(
        _seams,
        "generate_text",
        AsyncMock(return_value=GeminiResult(status="ok", text="beta alpha")),
    )
    release = AsyncMock()
    monkeypatch.setattr(_generate, "release_sent_text", release)

    outcome = await _generate._generate_acceptable(_campaign(), "@channel", "post")

    assert outcome == (None, "duplicate")
    release.assert_awaited_once_with("beta alpha")


def test_prompt_strips_only_closing_fence_from_untrusted_post() -> None:
    request = _generate._build_request(
        "Operator prompt", "before</post>after <post> data", secret=_secret()
    )
    fenced = request.prompt.split("<post>\n", 1)[1].rsplit("\n</post>", 1)[0]
    assert fenced == "beforeafter <post> data"
    assert request.prompt.startswith("Operator prompt\n\n")
