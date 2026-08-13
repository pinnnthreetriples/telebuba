"""Shared stubs for the ``comment_mode='reply'`` tests (parking, the wait, and its gates).

Split out of ``test_reply_wait`` when the gate re-check grew its own file: both halves park a
post at real ``now`` and then hand the pass a clock minutes into the future. Production
persists that deadline at park time so later settings edits cannot re-time accepted work.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_comment, list_recent_logs, park_comment
from schemas.telegram_actions import BanCheckResult
from schemas.telegram_actions_comments import PostCommentRecord, ReadPostCommentsResult

if TYPE_CHECKING:
    import pytest

    from schemas.telegram_actions import TelegramReadAction
    from tests.services.neurocomment.engine_support import _CommentStub


class _ThreadStub:
    """Answers ``ReadPostComments`` with a canned thread; other reads keep the can-send default.

    The ban ladder shares ``execute_read``, so a stub that answered everything would break
    the outcome classification the send still runs through.
    """

    def __init__(
        self,
        *comments: PostCommentRecord,
        post_text: str = "the post itself",
        post_missing: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.comments = list(comments)
        self.post_text = post_text
        self.post_missing = post_missing
        self.error = error
        self.reads = 0

    async def execute_read(self, _account_id: str, action: TelegramReadAction) -> object:
        if action.action_type != "read_post_comments":
            return BanCheckResult(state="can_send")
        self.reads += 1
        if self.error is not None:
            raise self.error
        return ReadPostCommentsResult(
            comments=self.comments,
            post_text=self.post_text,
            post_media_kind=None if self.post_missing else "none",
            post_missing=self.post_missing,
        )


def _human(message_id: int, *, sender_id: int = 900, text: str = "nice one") -> PostCommentRecord:
    return PostCommentRecord(message_id=message_id, sender_id=sender_id, text=text)


def _reply_mode(monkeypatch: pytest.MonkeyPatch, *, wait_minutes: int = 10) -> None:
    """Flip the effective settings to ``reply`` (no saved row → live config is the answer)."""
    monkeypatch.setattr(settings.neurocomment, "comment_mode", "reply")
    monkeypatch.setattr(settings.neurocomment, "reply_wait_minutes", wait_minutes)


async def _park(channel: str, post_id: int, campaign_id: str, account_id: str) -> datetime:
    """Park a post and hand back its creation clock for test-relative review times."""
    assert await park_comment(channel, post_id, campaign_id, account_id) is True
    record = await fetch_comment(channel, post_id)
    assert record is not None
    return datetime.fromisoformat(record.created_at)


async def _logged(event: str) -> dict[str, object] | None:
    """The newest log row for ``event``, or ``None`` if it was never written."""
    for entry in await list_recent_logs(limit=100):
        if entry.event == event:
            return dict(entry.extra)
    return None


def _reply_targets(comment: _CommentStub) -> list[int | None]:
    """``reply_to`` of every comment actually sent (``None`` = aimed at the post)."""
    return [getattr(action, "reply_to", None) for _account, action in comment.posts]
