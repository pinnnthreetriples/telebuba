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
    list_recent_logs,
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


class _CommitError(RuntimeError):
    """A named class, because the report's whole value is saying which fault it was."""


async def _ambiguous_event() -> tuple[str, object]:
    """The single settlement report an ambiguous dispatch owes the operator."""
    reported = [
        entry
        for entry in await list_recent_logs(limit=100)
        if entry.event.startswith("neurocomment_dispatch_") and entry.event.endswith("_unknown")
    ]
    assert len(reported) == 1
    return reported[0].event, reported[0].extra["error_type"]


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
async def test_deleted_account_is_terminal_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gone account is the one refusal worth spending the post on.

    Its transient siblings (warming, mid-handoff) raise the parent class and are
    released for retry instead — see ``test_account_unavailable_drop``.
    """
    event, limits = await _seed(13)
    account_id = "acc-1"

    async def _deleted(*_args: object, **_kwargs: object) -> ActionResult:
        raise _seams.NeurocommentAccountDeletedError(account_id)

    monkeypatch.setattr(_seams, "execute", _deleted)

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
async def test_a_comment_that_lands_after_its_lease_is_revoked_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fail-closed case the whole boundary exists for, driven through the real seam.

    Stop lands while the send is in flight. ``_seams.execute`` re-checks the generation
    AFTER the gateway returns, and a comment that may well be live on Telegram must not
    be re-sent by the next attempt -- so the post is settled ambiguous rather than
    released. Nothing else in the suite makes the real seam raise
    ``NeurocommentLeaseLostAfterDispatchError``; the stubs all raise it by hand.
    """
    event, limits = await _seed(15)
    live = True
    sent: list[TelegramAction] = []

    async def _gateway(
        account_id: str,
        action: TelegramAction,
        *,
        domain: str = "neurocomment",  # noqa: ARG001 - mirrors the real signature
    ) -> ActionResult:
        nonlocal live
        sent.append(action)
        live = False  # operator Stop, landing while Telegram already has the comment
        return ActionResult(
            status="ok",
            action_type=action.action_type,
            account_id=account_id,
            message_id=88,
        )

    monkeypatch.setattr(_seams, "_gateway_execute", _gateway)

    with _seams.generation_scope(lambda: live):
        outcome = await _generate._dispatch_reserved_comment(event, "acc-1", "too late", limits)

    assert outcome == PipelineOutcome.AMBIGUOUS
    assert len(sent) == 1  # the send really happened: that is why it cannot be retried
    inbox, comment = _durable_rows()
    assert inbox["stage"] == InboxStage.DISPATCHING
    assert comment["status"] == "failed"
    assert await _ambiguous_event() == (
        "neurocomment_dispatch_outcome_unknown",
        "NeurocommentLeaseLostAfterDispatchError",
    )


@pytest.mark.asyncio
async def test_a_delivered_comment_whose_bookkeeping_faults_is_ambiguous_not_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram accepted the comment and the write recording that did not.

    ``_classify_post`` runs after the boundary, so a fault in it says nothing about
    whether the comment landed -- it did. The post is settled ambiguous under its own
    event code, which is the only thing that tells the operator the difference between
    "sent, unrecorded" and "never sent".
    """
    event, limits = await _seed(16)

    async def _execute(account_id: str, action: TelegramAction) -> ActionResult:
        return ActionResult(
            status="ok",
            action_type=action.action_type,
            account_id=account_id,
            message_id=99,
        )

    async def _fault(*_args: object) -> None:
        raise _CommitError

    monkeypatch.setattr(_seams, "execute", _execute)
    monkeypatch.setattr(_generate, "_classify_post", _fault)

    outcome = await _generate._dispatch_reserved_comment(event, "acc-1", "recorded badly", limits)

    assert outcome == PipelineOutcome.AMBIGUOUS
    inbox, comment = _durable_rows()
    assert inbox["stage"] == InboxStage.DISPATCHED  # the send is durably known to be done
    assert comment["status"] == "failed"
    assert await _ambiguous_event() == ("neurocomment_dispatch_commit_unknown", "_CommitError")


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
