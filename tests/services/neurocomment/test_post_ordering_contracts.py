"""Ordering and repeated-delivery contracts around the atomic post claim."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from schemas.neurocomment import NeurocommentCampaign
from schemas.telegram_actions import NewPostEvent
from services.neurocomment import _filters, _state, engine

pytestmark = pytest.mark.usefixtures("isolate_engine")


def _campaign() -> NeurocommentCampaign:
    return NeurocommentCampaign(
        campaign_id="campaign",
        name="Campaign",
        prompt="Reply",
        status="active",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _event() -> NewPostEvent:
    return NewPostEvent(channel="@channel", post_id=31, text="post")


def _patch_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine, "fetch_active_campaign_for_channel", AsyncMock(return_value=_campaign())
    )
    monkeypatch.setattr(_filters, "filter_reason", lambda _event: None)
    monkeypatch.setattr(_state, "channel_in_backoff", lambda *_a: False)
    monkeypatch.setattr(_state, "is_channel_in_challenge_backoff", lambda *_a: False)
    monkeypatch.setattr(
        engine, "_select_account", AsyncMock(return_value=engine._Selection("account", None))
    )


@pytest.mark.asyncio
async def test_quota_is_rechecked_before_atomic_claim_and_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_path(monkeypatch)
    order: list[str] = []

    async def quota(*_args: object) -> None:
        order.append("quota")

    async def claim(*_args: object) -> bool:
        order.append("claim")
        return True

    async def generate(*_args: object) -> None:
        order.append("generate")

    monkeypatch.setattr(engine, "_account_quota_block_reason", quota)
    monkeypatch.setattr(engine, "claim_comment", claim)
    monkeypatch.setattr(engine, "_generate_and_post", generate)

    await engine._handle_new_post(_event())

    assert order == ["quota", "claim", "generate"]


@pytest.mark.asyncio
async def test_repeated_same_post_generates_only_for_claim_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_path(monkeypatch)
    monkeypatch.setattr(engine, "_account_quota_block_reason", AsyncMock(return_value=None))
    claim = AsyncMock(side_effect=[True, False])
    generate = AsyncMock()
    monkeypatch.setattr(engine, "claim_comment", claim)
    monkeypatch.setattr(engine, "_generate_and_post", generate)

    await engine._handle_new_post(_event())
    await engine._handle_new_post(_event())

    assert claim.await_count == 2
    generate.assert_awaited_once_with(_event(), _campaign(), "account")


@pytest.mark.asyncio
async def test_quota_block_never_attempts_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_path(monkeypatch)
    monkeypatch.setattr(engine, "_account_quota_block_reason", AsyncMock(return_value="quota_day"))
    claim = AsyncMock()
    log = AsyncMock()
    monkeypatch.setattr(engine, "claim_comment", claim)
    monkeypatch.setattr(engine, "log_event", log)

    await engine._handle_new_post(_event())

    claim.assert_not_awaited()
    log.assert_awaited_once_with(
        "INFO",
        "neurocomment_no_account_available",
        extra={"channel": "@channel", "post_id": 31, "reason": "quota_day"},
    )
