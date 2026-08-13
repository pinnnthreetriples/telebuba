"""Reply-mode dispatch keeps the durable inbox's fail-closed boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from core.db import (
    _get_engine,
    claim_comment,
    claim_pending_posts,
    enqueue_post,
)
from core.repositories.neurocomment._tables import (
    _neurocomment_comments,
    _neurocomment_inbox,
)
from schemas.neurocomment_pipeline import InboxStage, PipelineOutcome
from schemas.telegram_actions import ActionResult, CommentOnPost, NewPostEvent
from schemas.telegram_actions_comments import PostCommentRecord
from services.neurocomment import _generate, _seams
from services.neurocomment.settings_store import load_settings
from tests.services.neurocomment.engine_support import _make_campaign

if TYPE_CHECKING:
    from sqlalchemy.engine import RowMapping

    from schemas.neurocomment import NeurocommentSettings
    from schemas.telegram_actions import TelegramAction

pytestmark = pytest.mark.usefixtures("isolate_engine")


async def _seed(post_id: int) -> tuple[NewPostEvent, NeurocommentSettings]:
    campaign_id = await _make_campaign("@chan", "acc-1")
    event = NewPostEvent(
        channel="@chan",
        post_id=post_id,
        text="hello",
        date_unix=int(datetime.now(UTC).timestamp()),
    )
    await enqueue_post(event)
    claimed = await claim_pending_posts(1, event.date_unix - 1)
    assert [item.post_id for item in claimed] == [post_id]
    assert await claim_comment(event.channel, post_id, campaign_id, "acc-1")
    return event, await load_settings()


def _durable_rows() -> tuple[RowMapping, RowMapping]:
    with _get_engine().connect() as connection:
        inbox = connection.execute(select(_neurocomment_inbox)).mappings().one()
        comment = connection.execute(select(_neurocomment_comments)).mappings().one()
    return inbox, comment


@pytest.mark.asyncio
async def test_reply_dispatch_carries_target_and_reaches_dispatched_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event, limits = await _seed(10)
    actions: list[TelegramAction] = []

    async def _execute(account_id: str, action: TelegramAction) -> ActionResult:
        actions.append(action)
        return ActionResult(
            status="ok",
            action_type=action.action_type,
            account_id=account_id,
            message_id=77,
        )

    async def _classify(*_args: object) -> None:
        return None

    monkeypatch.setattr(_seams, "execute", _execute)
    monkeypatch.setattr(_generate, "_classify_post", _classify)
    target = PostCommentRecord(message_id=101, sender_id=202, text="reader comment")

    outcome = await _generate._dispatch_reserved_comment(
        event,
        "acc-1",
        "a natural reply",
        limits,
        target=target,
    )

    assert outcome == PipelineOutcome.TERMINAL
    assert len(actions) == 1
    assert isinstance(actions[0], CommentOnPost)
    assert actions[0].reply_to == 101
    inbox, _comment = _durable_rows()
    assert inbox["stage"] == InboxStage.DISPATCHED


@pytest.mark.asyncio
async def test_cancelled_reply_dispatch_is_ambiguous_and_never_reopens_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event, limits = await _seed(11)

    async def _cancel(*_args: object, **_kwargs: object) -> ActionResult:
        raise asyncio.CancelledError

    monkeypatch.setattr(_seams, "execute", _cancel)
    target = PostCommentRecord(message_id=303, sender_id=404, text="reader comment")

    outcome = await _generate._dispatch_reserved_comment(
        event,
        "acc-1",
        "a natural reply",
        limits,
        target=target,
    )

    assert outcome == PipelineOutcome.AMBIGUOUS
    inbox, comment = _durable_rows()
    assert inbox["stage"] == InboxStage.DISPATCHING
    assert comment["status"] == "failed"


@pytest.mark.asyncio
async def test_generation_revoked_before_dispatch_releases_claim_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event, limits = await _seed(12)

    async def _revoked(*_args: object, **_kwargs: object) -> ActionResult:
        raise _seams.NeurocommentLeaseRevokedError

    monkeypatch.setattr(_seams, "execute", _revoked)

    outcome = await _generate._dispatch_reserved_comment(
        event,
        "acc-1",
        "retry after restart",
        limits,
    )

    assert outcome == PipelineOutcome.RETRYABLE
    with _get_engine().connect() as connection:
        comments = connection.execute(select(_neurocomment_comments)).mappings().all()
    assert comments == []


@pytest.mark.asyncio
async def test_unavailable_account_is_terminal_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event, limits = await _seed(13)
    account_id = "acc-1"

    async def _unavailable(*_args: object, **_kwargs: object) -> ActionResult:
        raise _seams.NeurocommentAccountUnavailableError(account_id)

    monkeypatch.setattr(_seams, "execute", _unavailable)

    outcome = await _generate._dispatch_reserved_comment(
        event,
        account_id,
        "never send after handoff",
        limits,
    )

    assert outcome == PipelineOutcome.TERMINAL
    _inbox, comment = _durable_rows()
    assert comment["status"] == "failed"


@pytest.mark.asyncio
async def test_dispatch_stage_storage_fault_is_retryable_before_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event, limits = await _seed(14)
    called = False

    async def _execute(_account_id: str, _action: TelegramAction) -> ActionResult:
        nonlocal called
        called = True
        return ActionResult(status="ok", action_type="comment_on_post", account_id="acc-1")

    async def _storage_fault(*_args: object) -> bool:
        message = "sqlite unavailable before dispatch"
        raise RuntimeError(message)

    monkeypatch.setattr(_seams, "execute", _execute)
    monkeypatch.setattr(_generate, "set_comment_dispatch_stage", _storage_fault)

    outcome = await _generate._dispatch_reserved_comment(
        event,
        "acc-1",
        "retry once storage returns",
        limits,
    )

    assert outcome == PipelineOutcome.RETRYABLE
    assert not called
    with _get_engine().connect() as connection:
        comments = connection.execute(select(_neurocomment_comments)).mappings().all()
    assert comments == []
