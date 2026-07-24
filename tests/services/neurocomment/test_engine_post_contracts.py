"""Observable orchestration contracts for a new post."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from schemas.neurocomment import NeurocommentCampaign
from schemas.telegram_actions import NewPostEvent
from services.neurocomment import _filters, _state, engine

pytestmark = pytest.mark.usefixtures("isolate_engine")


def _event() -> NewPostEvent:
    return NewPostEvent(channel="@channel", post_id=41, text="A useful post")


def _campaign() -> NeurocommentCampaign:
    return NeurocommentCampaign(
        campaign_id="campaign-1",
        name="Campaign",
        prompt="Reply",
        status="active",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_missing_campaign_logs_complete_noop_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "fetch_active_campaign_for_channel", AsyncMock(return_value=None))
    log = AsyncMock()
    monkeypatch.setattr(engine, "log_event", log)
    select = AsyncMock()
    monkeypatch.setattr(engine, "_select_account", select)

    await engine._handle_new_post(_event())

    select.assert_not_awaited()
    log.assert_awaited_once_with(
        "INFO",
        "neurocomment_no_campaign",
        extra={"channel": "@channel", "post_id": 41},
    )


@pytest.mark.asyncio
async def test_filtered_post_reports_reason_before_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine, "fetch_active_campaign_for_channel", AsyncMock(return_value=_campaign())
    )
    monkeypatch.setattr(_filters, "filter_reason", lambda _event: "too_old")
    log = AsyncMock()
    select = AsyncMock()
    monkeypatch.setattr(engine, "log_event", log)
    monkeypatch.setattr(engine, "_select_account", select)

    await engine._handle_new_post(_event())

    select.assert_not_awaited()
    log.assert_awaited_once_with(
        "INFO",
        "neurocomment_post_skipped",
        extra={"channel": "@channel", "post_id": 41, "reason": "too_old"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("deletion_backoff", "challenge_backoff"), [(True, False), (False, True)])
async def test_each_channel_backoff_blocks_selection(
    monkeypatch: pytest.MonkeyPatch,
    deletion_backoff: bool,  # noqa: FBT001
    challenge_backoff: bool,  # noqa: FBT001
) -> None:
    monkeypatch.setattr(
        engine, "fetch_active_campaign_for_channel", AsyncMock(return_value=_campaign())
    )
    monkeypatch.setattr(_filters, "filter_reason", lambda _event: None)
    monkeypatch.setattr(_state, "channel_in_backoff", lambda *_a: deletion_backoff)
    monkeypatch.setattr(_state, "is_channel_in_challenge_backoff", lambda *_a: challenge_backoff)
    select = AsyncMock()
    log = AsyncMock()
    monkeypatch.setattr(engine, "_select_account", select)
    monkeypatch.setattr(engine, "log_event", log)

    await engine._handle_new_post(_event())

    select.assert_not_awaited()
    log.assert_awaited_once()
    call = log.await_args
    assert call is not None
    assert call.args == ("INFO", "neurocomment_channel_cooled")


@pytest.mark.asyncio
async def test_lost_claim_has_no_generation_or_failure_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine, "fetch_active_campaign_for_channel", AsyncMock(return_value=_campaign())
    )
    monkeypatch.setattr(_filters, "filter_reason", lambda _event: None)
    monkeypatch.setattr(_state, "channel_in_backoff", lambda *_a: False)
    monkeypatch.setattr(_state, "is_channel_in_challenge_backoff", lambda *_a: False)
    monkeypatch.setattr(
        engine, "_select_account", AsyncMock(return_value=engine._Selection("account", None))
    )
    monkeypatch.setattr(engine, "_account_quota_block_reason", AsyncMock(return_value=None))
    monkeypatch.setattr(engine, "claim_comment", AsyncMock(return_value=False))
    generate = AsyncMock()
    failed = AsyncMock()
    monkeypatch.setattr(engine, "_generate_and_post", generate)
    monkeypatch.setattr(engine, "mark_comment_failed", failed)

    await engine._handle_new_post(_event())

    generate.assert_not_awaited()
    failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_claim_failure_releases_claim_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine, "fetch_active_campaign_for_channel", AsyncMock(return_value=_campaign())
    )
    monkeypatch.setattr(_filters, "filter_reason", lambda _event: None)
    monkeypatch.setattr(_state, "channel_in_backoff", lambda *_a: False)
    monkeypatch.setattr(_state, "is_channel_in_challenge_backoff", lambda *_a: False)
    monkeypatch.setattr(
        engine, "_select_account", AsyncMock(return_value=engine._Selection("account", None))
    )
    monkeypatch.setattr(engine, "_account_quota_block_reason", AsyncMock(return_value=None))
    monkeypatch.setattr(engine, "claim_comment", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "_generate_and_post", AsyncMock(side_effect=RuntimeError("boom")))
    failed = AsyncMock()
    monkeypatch.setattr(engine, "mark_comment_failed", failed)

    with pytest.raises(RuntimeError, match="boom"):
        await engine._handle_new_post(_event())

    failed.assert_awaited_once_with("@channel", 41)


@pytest.mark.asyncio
async def test_listener_guard_logs_exception_identity_and_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "_handle_new_post", AsyncMock(side_effect=ValueError("bad post")))
    log = AsyncMock()
    monkeypatch.setattr(engine, "log_event", log)

    await engine.handle_new_post(_event())

    log.assert_awaited_once_with(
        "ERROR",
        "neurocomment_pipeline_failed",
        extra={
            "channel": "@channel",
            "post_id": 41,
            "error_type": "ValueError",
            "message": "bad post",
        },
    )
