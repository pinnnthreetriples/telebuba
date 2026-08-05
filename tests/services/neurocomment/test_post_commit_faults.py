"""A delivered comment must lift the channel's pause even if the bookkeeping after it faults.

``_outcomes._commit_delivered`` runs every write a delivery owes under ONE swallowing
``except``, because a comment Telegram accepted must never be flipped to ``failed``. That
made the ORDER of those writes the only thing protecting each of them: a fault in an optional
write left a delivered comment beside an uncleared pause, and ``review_expired_pauses`` — the
sweep pass that judges an expired deadline — then unlinked a channel that had just proved it
takes comments. ``clear_write_failures`` therefore goes first, and the swallowing handler names
the exception class, which is the only thing that made a drop like that diagnosable at all.

Own module because ``test_channel_pause`` is close to the 700-line test cap; the pause rule
itself and the reclaim races live there and in ``test_claim_integrity``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.db import (
    bump_channel_pause,
    fetch_channel_paused_until,
    fetch_comment,
    list_campaign_channels,
    list_recent_logs,
)
from schemas.telegram_actions import NewPostEvent
from services.neurocomment import _outcomes, engine
from tests.services.neurocomment.engine_support import _CommentStub, _make_campaign, _patch_io

pytestmark = pytest.mark.usefixtures("isolate_engine")

_CHANNEL = "@chan"


class _BookkeepingError(RuntimeError):
    """A named class, because the whole claim is that the log reports which one it was."""


async def _a_spent_pause_round() -> None:
    """One round on ``@chan`` whose window has just run out.

    The state K write failures leave behind, written through the repository rather than by
    gating a post: the deadline is a second in the past, which is what lets the engine take
    the next post at all — and what puts the channel one sweep tick away from being judged.
    """
    ended = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    assert await bump_channel_pause(_CHANNEL, ended) is not None


@pytest.mark.asyncio
async def test_a_delivered_comment_lifts_the_pause_before_the_writes_that_may_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pause is cleared first, so an optional write faulting cannot cost the channel.

    ``resolve_pending_outcome`` is the optional one — it confirms a solver click and is a no-op
    for a channel that never issued a challenge. With it running BEFORE the clear, a fault in it
    left the round counter and the expired deadline standing over a comment that had just landed,
    and the next sweep tick read them as the channel's verdict.
    """
    campaign_id = await _make_campaign(_CHANNEL, "acc-1")
    await _a_spent_pause_round()

    async def _fault(*_args: object, **_kwargs: object) -> None:
        raise _BookkeepingError

    # Patched where ``_outcomes`` imported it: that binding is what the commit block calls.
    monkeypatch.setattr(_outcomes, "resolve_pending_outcome", _fault)
    _patch_io(monkeypatch, comment=_CommentStub(status="ok"))

    await engine.handle_new_post(NewPostEvent(channel=_CHANNEL, post_id=1, text="hello world"))

    # The comment landed and the row says so — the fault must not have flipped it.
    delivered = await fetch_comment(_CHANNEL, 1)
    assert delivered is not None
    assert delivered.status == "posted"
    # ...and the channel demonstrably works: no deadline for the sweep to judge, no rounds
    # carried into its next bad day.
    assert await fetch_channel_paused_until(_CHANNEL) is None
    assert [link.pause_rounds for link in (await list_campaign_channels(campaign_id)).links] == [0]
    # The half-written commit is reported, and says which fault it was: without the class name
    # the line only said something broke, which is why a channel dropped by an uncleared pause
    # could never be traced back to the write that left it there.
    failures = [
        entry
        for entry in await list_recent_logs(limit=100)
        if entry.event == "neurocomment_post_commit_failed"
    ]
    assert len(failures) == 1
    assert failures[0].extra["error_type"] == "_BookkeepingError"
    assert failures[0].extra["channel"] == _CHANNEL
    assert failures[0].extra["post_id"] == 1
