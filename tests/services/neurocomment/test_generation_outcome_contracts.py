"""Post outcome contracts, including partial commits."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from schemas.telegram_actions import ActionResult, ActionStatus, NewPostEvent
from services.neurocomment import _generate, _state

pytestmark = pytest.mark.usefixtures("isolate_engine")


def _event() -> NewPostEvent:
    return NewPostEvent(channel="@channel", post_id=7, text="post")


def _result(
    status: ActionStatus, *, error_type: str | None = None, seconds: int | None = None
) -> ActionResult:
    return ActionResult(
        status=status,
        action_type="comment_on_post",
        account_id="account",
        message_id=99 if status == "ok" else None,
        error_type=error_type,
        flood_wait_seconds=seconds,
    )


@pytest.mark.asyncio
async def test_success_clears_cooldown_and_records_exact_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear = Mock()
    monkeypatch.setattr(_state, "clear_cooldown", clear)
    posted = AsyncMock()
    resolved = AsyncMock(return_value=False)
    log = AsyncMock()
    monkeypatch.setattr(_generate, "mark_comment_posted", posted)
    monkeypatch.setattr(_generate, "resolve_pending_outcome", resolved)
    monkeypatch.setattr(_generate, "log_event", log)

    await _generate._classify_post(_event(), "account", "comment", _result("ok"))

    clear.assert_called_once_with("account", "@channel")
    posted.assert_awaited_once_with("@channel", 7, comment_text="comment", comment_msg_id=99)
    log.assert_awaited_once_with(
        "INFO",
        "neurocomment_posted",
        account_id="account",
        extra={"channel": "@channel", "post_id": 7},
    )


@pytest.mark.asyncio
async def test_post_commit_failure_is_logged_without_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_generate, "mark_comment_posted", AsyncMock(side_effect=OSError("db")))
    failed = AsyncMock()
    released = AsyncMock()
    log = AsyncMock()
    monkeypatch.setattr(_generate, "mark_comment_failed", failed)
    monkeypatch.setattr(_generate, "release_sent_text", released)
    monkeypatch.setattr(_generate, "log_event", log)

    await _generate._classify_post(_event(), "account", "comment", _result("ok"))

    failed.assert_not_awaited()
    released.assert_not_awaited()
    log.assert_awaited_once()
    call = log.await_args
    assert call is not None
    assert call.args == ("ERROR", "neurocomment_post_commit_failed")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "scope"),
    [
        ("flood_wait", None),
        ("peer_flood", None),
        ("premium_wait", None),
        ("slow_mode_wait", "@channel"),
    ],
)
async def test_wait_families_fail_release_and_apply_correct_scope(
    monkeypatch: pytest.MonkeyPatch, status: ActionStatus, scope: str | None
) -> None:
    failed = AsyncMock()
    released = AsyncMock()
    cooldown = Mock()
    monkeypatch.setattr(_generate, "mark_comment_failed", failed)
    monkeypatch.setattr(_generate, "release_sent_text", released)
    monkeypatch.setattr(_generate, "_apply_cooldown", cooldown)
    monkeypatch.setattr(_generate, "log_event", AsyncMock())

    await _generate._classify_post(_event(), "account", "comment", _result(status, seconds=30))

    released.assert_awaited_once_with("comment")
    failed.assert_awaited_once_with("@channel", 7)
    cooldown.assert_called_once_with("account", 30, scope)


@pytest.mark.asyncio
async def test_ban_and_gate_are_distinct_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    banned = AsyncMock()
    readiness = AsyncMock()
    resolved = AsyncMock(return_value=False)
    monkeypatch.setattr(_generate, "mark_pair_banned", banned)
    monkeypatch.setattr(_generate, "upsert_readiness", readiness)
    monkeypatch.setattr(_generate, "resolve_pending_outcome", resolved)
    monkeypatch.setattr(_generate, "mark_comment_failed", AsyncMock())
    monkeypatch.setattr(_generate, "release_sent_text", AsyncMock())
    monkeypatch.setattr(_generate, "log_event", AsyncMock())

    await _generate._classify_post(
        _event(), "account", "a", _result("failed", error_type="UserBannedInChannelError")
    )
    await _generate._classify_post(
        _event(), "account", "b", _result("failed", error_type="ChatWriteForbiddenError")
    )

    banned.assert_awaited_once_with("account", "@channel")
    readiness.assert_awaited_once_with(
        "account", "@channel", joined=True, captcha_passed=False, ready=False
    )
    resolved.assert_awaited_once_with("account", "@channel", "failed")
