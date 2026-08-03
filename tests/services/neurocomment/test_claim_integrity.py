"""A claim must survive its own worker, and a delivered comment must stay watchable.

Two faults with one root: between winning the claim and resolving it the worker wrote
nothing, so ``reclaim_stale_claims`` could only judge the row by its age and failed live
attempts out from under themselves (#10 of the audit). When that happens the send still
lands, but ``mark_comment_posted`` refuses to re-transition a terminal row — it wrote
NOTHING, message id included, while logging success — so the comment stayed live under
the post and permanently invisible to the deletion sweep, which can only look at rows
carrying an id (#11). No double-post is possible here (the claim row precedes the send
under ``ON CONFLICT DO NOTHING``), so the damage is undercount plus an orphan nobody
watches — that is what these tests pin.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    claim_comment,
    fetch_comment,
    list_delivered_comments_since,
    list_recent_logs,
    mark_comment_failed,
    record_comment_msg_id,
)
from schemas.telegram_actions import CheckMessagesAliveResult, NewPostEvent
from services.neurocomment import _seams, _state, _sweep, engine
from tests.services.neurocomment.engine_support import (
    _CommentStub,
    _GenStub,
    _make_campaign,
    _patch_io,
)

if TYPE_CHECKING:
    from schemas.gemini import GeminiResult
    from schemas.telegram_actions import ActionResult, CheckMessagesAlive, TelegramAction

pytestmark = pytest.mark.usefixtures("isolate_engine")


def _age_claim(channel: str, post_id: int, *, minutes: float) -> None:
    """Backdate a claim's stamps — the row as a long wait between claim and send leaves it."""
    aged = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_comments SET created_at = ?, updated_at = ? "
            "WHERE channel = ? AND post_id = ?",
            (aged, aged, channel, post_id),
        )


class _SlowGen(_GenStub):
    """Generation that outlasts the reclaim cutoff — the shared Gemini throttle waiting."""

    async def generate_text(self, _request: object) -> GeminiResult:
        _age_claim("@a", 10, minutes=60)
        return await super().generate_text(_request)


class _SweptComment(_CommentStub):
    """A stale-claim sweep pass ticks while this send is in flight.

    ``ahead_seconds`` moves the pass's clock forward, so a test can choose whether the
    cutoff catches the claim: 0 is the honest tick (cutoff = now - 900s), a large value
    is a cutoff that catches even a beaten claim — a misfire, or the crash-between-send-
    and-commit it is indistinguishable from.
    """

    def __init__(self, *, ahead_seconds: float = 0.0) -> None:
        super().__init__(status="ok")
        self.ahead_seconds = ahead_seconds

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        await _sweep._reclaim_stale_claims(
            datetime.now(UTC) + timedelta(seconds=self.ahead_seconds),
        )
        return await super().execute(account_id, action)


@pytest.mark.asyncio
async def test_heartbeat_keeps_the_reclaim_off_a_claim_that_is_still_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An hour of generation, then a real sweep tick mid-send: the claim must survive."""
    await _make_campaign("@a", "acc-1")
    comment = _SweptComment()
    _patch_io(monkeypatch, comment=comment, gen=_SlowGen("a nice comment"))

    await engine.handle_new_post(NewPostEvent(channel="@a", post_id=10, text="hi there"))

    assert len(comment.posts) == 1  # the send happened
    row = await fetch_comment("@a", 10)
    assert row is not None
    assert row.status == "posted"  # not failed out from under the worker that sent it
    assert row.comment_msg_id == 555


@pytest.mark.asyncio
async def test_a_reclaimed_claim_still_records_the_delivered_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row loses the verdict (terminal is terminal) but must keep the fact."""
    campaign_id = await _make_campaign("@a", "acc-1")
    comment = _SweptComment(ahead_seconds=1000)  # a cutoff that catches even a beaten claim
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@a", post_id=10, text="hi there"))

    row = await fetch_comment("@a", 10)
    assert row is not None
    # Unchanged on purpose: the claim row is the idempotency gate, so a terminal status is
    # never re-transitioned and the post is not handed back to anyone.
    assert row.status == "failed"
    assert row.comment_msg_id == 555  # ... but the comment IS live, and now says so
    day_ago = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    delivered = (await list_delivered_comments_since(campaign_id, day_ago)).comments
    assert [c.comment_msg_id for c in delivered] == [555]
    logs = await list_recent_logs(limit=50)
    # The log used to claim ``neurocomment_posted`` over a row reading ``failed``.
    assert [e for e in logs if e.event == "neurocomment_posted_after_reclaim"]


@pytest.mark.asyncio
async def test_sweep_watches_a_delivered_comment_recorded_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The orphan of a mid-send reclaim: still live under the post, so still swept."""
    campaign_id = await _make_campaign("@a", "acc-1")
    assert await claim_comment("@a", 10, campaign_id, "acc-1") is True
    await record_comment_msg_id("@a", 10, 555)
    await mark_comment_failed("@a", 10)
    monkeypatch.setattr(settings.neurocomment, "channel_backoff_min_deletions", 1)
    checked: list[int] = []

    async def fake_read(_account_id: str, action: CheckMessagesAlive) -> CheckMessagesAliveResult:
        checked.extend(action.message_ids)
        return CheckMessagesAliveResult(missing_ids=list(action.message_ids))

    monkeypatch.setattr(_seams, "execute_read", fake_read)

    await _sweep._sweep_once()

    assert checked == [555]  # the scan sees it at all
    row = await fetch_comment("@a", 10)
    assert row is not None
    assert row.deleted_at is not None  # and can stamp it, so the feed can show it went
    assert _state.channel_in_backoff("@a", datetime.now(UTC)) is True
