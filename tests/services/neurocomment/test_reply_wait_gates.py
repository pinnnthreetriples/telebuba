"""Tests for what a parked post must clear AGAIN before the wait finally sends it.

``comment_mode='reply'`` is the only path where the gates and the send are minutes to hours
apart. On the immediate path microseconds separate them, so nothing can change in between;
here an operator presses Пауза at 12:03 on a post parked at 12:00 and the sweep commented at
12:10 anyway. Every case below delivered a comment before the re-check existed.

Its own file rather than more of ``test_reply_wait``: that one is about WHOSE comment the wait
answers, this one about whether the post may be commented at all — and both sit near the
test-file size budget. Shared stubs live in ``reply_wait_support``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import bump_channel_pause, fetch_comment, upsert_readiness
from core.repositories.neurocomment import set_campaign_status
from services.neurocomment import _reply_wait, _seams, _state
from tests.services.neurocomment.engine_support import (
    _CommentStub,
    _GenStub,
    _make_campaign,
    _patch_io,
)
from tests.services.neurocomment.reply_wait_support import (
    _human,
    _logged,
    _park,
    _reply_mode,
    _reply_targets,
    _ThreadStub,
)

if TYPE_CHECKING:
    from datetime import datetime

pytestmark = pytest.mark.usefixtures("isolate_engine")

_CHANNEL = "@chan"


async def _close_gate(gate: str, campaign_id: str, parked_at: datetime) -> None:
    """Shut one of the engine's gates the way production shuts it, after the post was parked."""
    if gate in {"paused", "archived"}:
        await set_campaign_status(campaign_id, gate)
    elif gate == "channel_paused":
        await bump_channel_pause(_CHANNEL, (parked_at + timedelta(hours=24)).isoformat())
    elif gate == "cooldown":
        await _state.set_cooldown("acc-1", parked_at + timedelta(hours=1), _CHANNEL)
    else:
        await upsert_readiness("acc-1", _CHANNEL, joined=True, captcha_passed=True, ready=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gate", "event", "reason"),
    [
        ("paused", "neurocomment_no_campaign", None),
        ("archived", "neurocomment_no_campaign", None),
        ("channel_paused", "neurocomment_channel_cooled", None),
        ("cooldown", "neurocomment_no_account_available", "cooldown"),
        ("not_ready", "neurocomment_no_account_available", "not_ready"),
    ],
)
async def test_a_gate_shut_during_the_wait_drops_the_parked_post(
    monkeypatch: pytest.MonkeyPatch,
    gate: str,
    event: str,
    reason: str | None,
) -> None:
    """Each gate is a statement about NOW, so each one is asked again before the send.

    The flood cooldown is the sharpest of them: it is Telegram asking this account to wait,
    and writing anyway is a knock on the door we were told not to knock on.
    """
    campaign_id = await _make_campaign(_CHANNEL, "acc-1")
    _reply_mode(monkeypatch)
    parked_at = await _park(_CHANNEL, 10, campaign_id, "acc-1")
    comment = _CommentStub(status="ok", message_id=555)
    gen = _GenStub("must not be generated")
    _patch_io(monkeypatch, comment=comment, gen=gen)
    thread = _ThreadStub(_human(100), _human(101))
    monkeypatch.setattr(_seams, "execute_read", thread.execute_read)
    await _close_gate(gate, campaign_id, parked_at)

    await _reply_wait.review_waiting_posts(parked_at + timedelta(minutes=1))

    assert comment.posts == []
    assert gen.calls == 0
    record = await fetch_comment(_CHANNEL, 10)
    assert record is not None
    # ``failed``, not left waiting: every one of these outlives the post's freshness, and a
    # ``waiting`` row goes on charging the account a quota slot for the whole pause.
    assert record.status == "failed"
    extra = await _logged(event)
    assert extra is not None
    assert extra.get("reason") == reason


@pytest.mark.asyncio
async def test_the_hourly_cap_is_re_read_before_a_parked_post_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two parked rows and a cap of one: the pass delivers one comment, not both.

    The quota window is measured from ``created_at``, so a post parked long enough ago has
    already dropped out of the hour it was admitted in and its sibling is admitted too.
    Nothing re-read the caps between the park and the send, so one pass then sent both — 2
    comments under a cap of 1, reachable on defaults at the top of ``reply_wait_minutes``.
    """
    campaign_id = await _make_campaign(_CHANNEL, "acc-1")
    _reply_mode(monkeypatch)
    monkeypatch.setattr(settings.neurocomment, "max_comments_per_hour", 1)
    parked_at = await _park(_CHANNEL, 10, campaign_id, "acc-1")
    await _park(_CHANNEL, 11, campaign_id, "acc-1")
    comment = _CommentStub(status="ok", message_id=555)
    _patch_io(monkeypatch, comment=comment)
    thread = _ThreadStub(_human(100), _human(101))
    monkeypatch.setattr(_seams, "execute_read", thread.execute_read)

    await _reply_wait.review_waiting_posts(parked_at + timedelta(minutes=11))

    assert len(comment.posts) == 1
    blocked = await _logged("neurocomment_no_account_available")
    assert blocked is not None
    assert blocked["reason"] == "quota_hour"


@pytest.mark.asyncio
async def test_a_row_far_past_its_deadline_is_dropped_unsent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop for a day, then Start, and the whole backlog would be commented in one pass.

    The deadline alone cannot bound this: it resolves a row only while the sweep RUNS, and the
    sweep task exists only while the listener does. An hour of missed ticks is forgiven (the
    existing tests send at +11 minutes on a ten-minute wait); past that the post is stale.
    """
    campaign_id = await _make_campaign(_CHANNEL, "acc-1")
    _reply_mode(monkeypatch)
    parked_at = await _park(_CHANNEL, 10, campaign_id, "acc-1")
    comment = _CommentStub(status="ok", message_id=555)
    gen = _GenStub("must not be generated")
    _patch_io(monkeypatch, comment=comment, gen=gen)
    thread = _ThreadStub(_human(100), _human(101))
    monkeypatch.setattr(_seams, "execute_read", thread.execute_read)

    await _reply_wait.review_waiting_posts(parked_at + timedelta(hours=2, minutes=10))

    assert comment.posts == []
    assert gen.calls == 0
    record = await fetch_comment(_CHANNEL, 10)
    assert record is not None
    assert record.status == "failed"
    skipped = await _logged("neurocomment_post_skipped")
    assert skipped is not None
    assert skipped["reason"] == "too_old"


@pytest.mark.asyncio
async def test_two_album_siblings_in_one_pass_answer_two_different_people(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Album items each fire their own post event but share ONE discussion thread.

    So two parked siblings resolved in the same tick read the same comments, and without a
    per-pass memory both aimed at the same person — two of our accounts answering one reader
    under one visible post, which is the swarm this mode exists not to look like.
    """
    campaign_id = await _make_campaign(_CHANNEL, "acc-1", "acc-2")
    _reply_mode(monkeypatch)
    parked_at = await _park(_CHANNEL, 10, campaign_id, "acc-1")
    await _park(_CHANNEL, 11, campaign_id, "acc-2")
    comment = _CommentStub(status="ok", message_id=555)
    # Two texts, because the duplicate check would refuse the second sibling a comment
    # identical to the first — a real guard, but not the one under test here.
    _patch_io(monkeypatch, comment=comment, gen=_GenStub("first take", "second take"))
    thread = _ThreadStub(*(_human(100 + i, sender_id=900 + i) for i in range(4)))
    monkeypatch.setattr(_seams, "execute_read", thread.execute_read)

    await _reply_wait.review_waiting_posts(parked_at + timedelta(minutes=1))

    # The first row takes 101 (never the opener); the second skips its author and takes 102.
    assert _reply_targets(comment) == [101, 102]
