"""Durable retry and fail-closed dispatch boundaries for the comment engine."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.db import fetch_comment
from schemas.neurocomment_pipeline import PipelineOutcome
from schemas.telegram_actions import NewPostEvent
from services.neurocomment import _generate, _seams, engine
from tests.services.neurocomment.engine_support import (
    _CommentStub,
    _FixedRng,
    _GenStub,
    _make_campaign,
)

if TYPE_CHECKING:
    from schemas.gemini import GeminiResult

pytestmark = pytest.mark.usefixtures("isolate_engine")


@pytest.mark.asyncio
async def test_exception_before_dispatch_releases_claim_for_durable_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A provider raise is durably pre-send, so the claim is released for inbox retry.
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
    assert record is None


@pytest.mark.asyncio
async def test_cancelled_before_dispatch_releases_claim_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Cancellation before dispatch is safe to retry and must still cancel the task.
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
    assert record is None


@pytest.mark.asyncio
async def test_dispatch_cleanup_faults_cannot_reopen_ambiguous_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _make_campaign("@chan", "acc-1")
    dispatch_error = "connection dropped during send"
    cleanup_error = "sqlite unavailable during cleanup"

    async def _dispatch_raises(*_args: object, **_kwargs: object) -> object:
        raise OSError(dispatch_error)

    async def _cleanup_raises(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError(cleanup_error)

    monkeypatch.setattr(_seams, "execute", _dispatch_raises)
    monkeypatch.setattr(_seams, "rng", _FixedRng())
    monkeypatch.setattr(_seams, "generate_text", _GenStub("a nice comment").generate_text)
    monkeypatch.setattr(_generate, "mark_comment_failed", _cleanup_raises)
    monkeypatch.setattr(_generate, "log_event", _cleanup_raises)

    outcome = await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert outcome == PipelineOutcome.AMBIGUOUS
    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "claimed"  # fail closed: release_claim was never called
