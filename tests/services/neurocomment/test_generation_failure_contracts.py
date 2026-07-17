"""Failure, cancellation, and partial-commit contracts for posting."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from schemas.neurocomment import NeurocommentCampaign
from schemas.telegram_actions import ActionResult, NewPostEvent
from services.neurocomment import _generate, _seams, _state

pytestmark = pytest.mark.usefixtures("isolate_engine")


def _event() -> NewPostEvent:
    return NewPostEvent(channel="@channel", post_id=17, text="post")


def _campaign() -> NeurocommentCampaign:
    return NeurocommentCampaign(
        campaign_id="campaign",
        name="Campaign",
        prompt="Reply",
        status="active",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _limits() -> SimpleNamespace:
    return SimpleNamespace(reply_delay_min_seconds=2.0, reply_delay_max_seconds=4.0)


@pytest.mark.asyncio
async def test_generation_exhaustion_fails_claim_without_loading_post_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _generate,
        "_generate_acceptable",
        AsyncMock(return_value=_generate._GenOutcome(None, "not_acceptable")),
    )
    failed = AsyncMock()
    settings_read = AsyncMock()
    log = AsyncMock()
    monkeypatch.setattr(_generate, "mark_comment_failed", failed)
    monkeypatch.setattr(_generate, "load_neuro_settings", settings_read)
    monkeypatch.setattr(_generate, "log_event", log)

    await _generate._generate_and_post(_event(), _campaign(), "account")

    failed.assert_awaited_once_with("@channel", 17)
    settings_read.assert_not_awaited()
    log.assert_awaited_once_with(
        "INFO",
        "neurocomment_generation_exhausted",
        account_id="account",
        extra={"channel": "@channel", "post_id": 17, "reason": "not_acceptable"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["settings", "delay", "gateway"])
async def test_every_pre_delivery_failure_releases_exact_and_semantic_claims(
    monkeypatch: pytest.MonkeyPatch, failure_stage: str
) -> None:
    text = "reserved comment"
    _generate._add_inflight("@channel", text, _generate.datetime.now(_generate.UTC))
    monkeypatch.setattr(
        _generate,
        "_generate_acceptable",
        AsyncMock(return_value=_generate._GenOutcome(text, None)),
    )
    release = AsyncMock()
    monkeypatch.setattr(_generate, "release_sent_text", release)
    monkeypatch.setattr(_seams, "rng", SimpleNamespace(uniform=lambda low, _high: low))
    if failure_stage == "settings":
        monkeypatch.setattr(
            _generate, "load_neuro_settings", AsyncMock(side_effect=RuntimeError("settings"))
        )
    else:
        monkeypatch.setattr(_generate, "load_neuro_settings", AsyncMock(return_value=_limits()))
    if failure_stage == "delay":
        monkeypatch.setattr(
            _generate.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError())
        )
    else:
        monkeypatch.setattr(_generate.asyncio, "sleep", AsyncMock())
    if failure_stage == "gateway":
        monkeypatch.setattr(_seams, "execute", AsyncMock(side_effect=ConnectionError("down")))
    else:
        monkeypatch.setattr(
            _seams,
            "execute",
            AsyncMock(
                return_value=ActionResult(
                    status="ok", action_type="comment_on_post", account_id="account"
                )
            ),
        )

    expected = asyncio.CancelledError if failure_stage == "delay" else Exception
    with pytest.raises(expected):
        await _generate._generate_and_post(_event(), _campaign(), "account")

    release.assert_awaited_once_with(text)
    assert "@channel" not in _generate._INFLIGHT


@pytest.mark.asyncio
async def test_successful_gateway_result_is_classified_after_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _generate,
        "_generate_acceptable",
        AsyncMock(return_value=_generate._GenOutcome("comment", None)),
    )
    monkeypatch.setattr(_generate, "load_neuro_settings", AsyncMock(return_value=_limits()))
    monkeypatch.setattr(_seams, "rng", SimpleNamespace(uniform=lambda low, high: (low + high) / 2))
    sleep = AsyncMock()
    result = ActionResult(
        status="failed", action_type="comment_on_post", account_id="account", error_type="x"
    )
    execute = AsyncMock(return_value=result)
    classify = AsyncMock()
    monkeypatch.setattr(_generate.asyncio, "sleep", sleep)
    monkeypatch.setattr(_seams, "execute", execute)
    monkeypatch.setattr(_generate, "_classify_post", classify)

    await _generate._generate_and_post(_event(), _campaign(), "account")

    sleep.assert_awaited_once_with(3.0)
    classify.assert_awaited_once_with(_event(), "account", "comment", result)


@pytest.mark.asyncio
async def test_successful_pending_challenge_resets_failure_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset = Mock()
    monkeypatch.setattr(_state, "reset_challenge_failures", reset)
    monkeypatch.setattr(_generate, "mark_comment_posted", AsyncMock())
    monkeypatch.setattr(_generate, "resolve_pending_outcome", AsyncMock(return_value=True))
    monkeypatch.setattr(_generate, "log_event", AsyncMock())

    await _generate._classify_post(
        _event(),
        "account",
        "comment",
        ActionResult(
            status="ok", action_type="comment_on_post", account_id="account", message_id=9
        ),
    )

    reset.assert_called_once_with("@channel")


@pytest.mark.asyncio
async def test_gate_without_pending_challenge_does_not_increment_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_generate, "release_sent_text", AsyncMock())
    monkeypatch.setattr(_generate, "mark_comment_failed", AsyncMock())
    monkeypatch.setattr(_generate, "upsert_readiness", AsyncMock())
    monkeypatch.setattr(_generate, "resolve_pending_outcome", AsyncMock(return_value=False))
    register = AsyncMock()
    monkeypatch.setattr(_generate, "_register_challenge_failure", register)
    monkeypatch.setattr(_generate, "log_event", AsyncMock())

    await _generate._classify_post(
        _event(),
        "account",
        "comment",
        ActionResult(
            status="failed",
            action_type="comment_on_post",
            account_id="account",
            error_type="ChatGuestSendForbiddenError",
        ),
    )

    register.assert_not_awaited()
