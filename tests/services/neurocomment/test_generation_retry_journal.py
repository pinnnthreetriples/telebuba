"""What the operator reads about a comment that had to be written more than once.

The regeneration ladder was invisible: the only line it ever produced was the exhaustion
at the very end, so a post that took three rounds of paid Gemini calls looked exactly like
one that succeeded first try. These tests pin the counter — and, just as importantly, its
absence on the first round, which happens on every single post and must stay silent.

Own module because ``test_engine_generation`` is already at 554 lines against the
700-line test cap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.db import fetch_comment, list_recent_logs
from schemas.telegram_actions import NewPostEvent
from services.neurocomment import _sweep, engine
from tests.services.neurocomment.engine_support import (
    _CommentStub,
    _GenStub,
    _make_campaign,
    _patch_io,
)

if TYPE_CHECKING:
    from schemas.gemini import GeminiResult

pytestmark = pytest.mark.usefixtures("isolate_engine")

# Well past ``comment_max_words``, so the round is rejected on length every time.
_TOO_LONG = "word " * 99


async def _journal() -> list[tuple[str, str, object]]:
    """The generation lines, oldest first: ``(level, event, extra["reason"])``."""
    return [
        (entry.level, entry.event, entry.extra.get("reason"))
        for entry in reversed(await list_recent_logs(limit=50))
        if entry.event in ("neurocomment_generation_retry", "neurocomment_generation_exhausted")
    ]


@pytest.mark.asyncio
async def test_a_comment_that_worked_first_try_writes_no_retry_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The normal path on every post: one line here would double the feed for nothing."""
    await _make_campaign("@chan", "acc-1")
    _patch_io(monkeypatch, comment=_CommentStub(status="ok"), gen=_GenStub("a nice comment"))

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "posted"
    assert await _journal() == []


@pytest.mark.asyncio
async def test_a_rejected_round_says_which_retry_it_bought(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One round failed, the next worked: the retry is the only trace it took two."""
    await _make_campaign("@chan", "acc-1")
    gen = _GenStub(_TOO_LONG, "a nice comment")
    _patch_io(monkeypatch, comment=_CommentStub(status="ok"), gen=gen)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert gen.calls == 2
    assert await _journal() == [("INFO", "neurocomment_generation_retry", "1/2")]


@pytest.mark.asyncio
async def test_the_retry_line_carries_the_post_it_belongs_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A burst of posts shares the feed, so the line has to name its own channel/post."""
    await _make_campaign("@chan", "acc-1")
    _patch_io(monkeypatch, comment=_CommentStub(status="ok"), gen=_GenStub(_TOO_LONG, "nice one"))

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=42, text="hi"))

    retry = next(
        entry
        for entry in await list_recent_logs(limit=50)
        if entry.event == "neurocomment_generation_retry"
    )
    assert retry.account_id == "acc-1"
    assert (retry.extra["channel"], retry.extra["post_id"]) == ("@chan", 42)


@pytest.mark.asyncio
async def test_every_round_failing_counts_the_budget_out_and_still_ends_with_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The counter must not push the failure's cause off the last line — it rides there.

    ``neurocomment_generation_exhausted`` keeps spending ``reason`` on WHY the ladder gave
    up (``too_long`` here); the count lives on the retry lines before it.
    """
    await _make_campaign("@chan", "acc-1")
    gen = _GenStub(_TOO_LONG)  # cycles: every round is too long
    _patch_io(monkeypatch, comment=_CommentStub(status="ok"), gen=gen)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert gen.calls == 3  # one try plus max_retries (2)
    assert await _journal() == [
        ("INFO", "neurocomment_generation_retry", "1/2"),
        ("INFO", "neurocomment_generation_retry", "2/2"),
        ("INFO", "neurocomment_generation_exhausted", "too_long"),
    ]


class _ClaimLosingGen(_GenStub):
    """Round one is rejected on length, and the claim is reclaimed while it runs.

    So round two never happens: its claim beat abandons the ladder first. Nothing was
    spent, so nothing may be counted.
    """

    def __init__(self) -> None:
        super().__init__(_TOO_LONG, "a nice comment")

    async def generate_text(self, _request: object) -> GeminiResult:
        if self.calls == 0:
            await _sweep._reclaim_stale_claims(datetime.now(UTC) + timedelta(seconds=1000))
        return await super().generate_text(_request)


@pytest.mark.asyncio
async def test_a_round_that_never_ran_is_never_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The abandon returns before the line, so the budget shows no attempt it did not pay."""
    await _make_campaign("@chan", "acc-1")
    gen = _ClaimLosingGen()
    _patch_io(monkeypatch, comment=_CommentStub(status="ok"), gen=gen)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert gen.calls == 1  # round two paid for no generation ...
    assert await _journal() == [  # ... and claimed no retry either
        ("INFO", "neurocomment_generation_exhausted", "claim_lost"),
    ]
