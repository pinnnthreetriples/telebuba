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
    fetch_channel_paused_until,
    fetch_comment,
    list_delivered_comments_since,
    list_recent_logs,
    mark_comment_failed,
    mark_comments_deleted,
    record_comment_msg_id,
    release_claim,
    touch_comment_claim,
)
from schemas.telegram_actions import CheckMessagesAliveResult, NewPostEvent
from services.neurocomment import _seams, _sweep, engine
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


def _advance_clock(channel: str, post_id: int, *, seconds: float) -> None:
    """Move the world forward by ``seconds``: every stamp on the row slides back that far.

    RELATIVE, unlike ``_age_claim``, and that is the whole point — a heartbeat that landed a
    moment ago is still recent afterwards, so a test can spend an hour of wall clock inside
    the pipeline without erasing the beats the pipeline made while spending it.
    """
    with _get_engine().begin() as connection:
        row = connection.exec_driver_sql(
            "SELECT created_at, updated_at FROM neurocomment_comments "
            "WHERE channel = ? AND post_id = ?",
            (channel, post_id),
        ).first()
        if row is None:  # pragma: no cover - the claim always exists by the time we sleep
            return
        shifted = [
            (datetime.fromisoformat(str(value)) - timedelta(seconds=seconds)).isoformat()
            for value in row
        ]
        connection.exec_driver_sql(
            "UPDATE neurocomment_comments SET created_at = ?, updated_at = ? "
            "WHERE channel = ? AND post_id = ?",
            (*shifted, channel, post_id),
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
    # Recorded, and that is ALL a deletion does: the escalating back-off it used to trip
    # was removed by operator decision, so the channel's next post is commented as usual.
    assert await fetch_channel_paused_until("@a") is None


class _RegeneratingSlowGen(_GenStub):
    """Three hours pass in the generation ladder, then an honest sweep tick mid-round.

    The first candidate is rejected on word count, so the ladder runs a second round — which
    is where the hours sit: BETWEEN two beats, not inside one un-sliceable await. A single
    pre-send beat covers none of it, because the reclaim fires while generation is still
    running and the row it reads is three hours old.
    """

    def __init__(self) -> None:
        super().__init__("word " * 99, "a nice comment")  # too_long, then acceptable

    async def generate_text(self, _request: object) -> GeminiResult:
        if self.calls == 0:
            _advance_clock("@a", 10, seconds=3 * 3600)
        else:
            # Round two has just beaten the claim at the top of the loop, so an honest tick
            # (cutoff = now - 900s) must find nothing to reclaim.
            await _sweep._reclaim_stale_claims(datetime.now(UTC))
        return await super().generate_text(_request)


@pytest.mark.asyncio
async def test_heartbeat_covers_a_generation_ladder_that_outlives_the_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One beat per round, so the widest gap is a round — not the whole ladder."""
    await _make_campaign("@a", "acc-1")
    comment = _CommentStub(status="ok")
    _patch_io(monkeypatch, comment=comment, gen=_RegeneratingSlowGen())

    await engine.handle_new_post(NewPostEvent(channel="@a", post_id=10, text="hi there"))

    assert len(comment.posts) == 1
    row = await fetch_comment("@a", 10)
    assert row is not None
    assert row.status == "posted"


@pytest.mark.asyncio
async def test_heartbeat_covers_a_reply_delay_that_outlives_the_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The delay the operator sets directly, spent inside the claim, sliced and beaten.

    30 minutes is legal on purpose: the write schema carries no upper bound on this field,
    because a cap there would 422 every unrelated Settings edit made on a value the UI had
    already accepted. What makes it safe is the slicing, and this is where that is pinned.
    """
    await _make_campaign("@a", "acc-1")
    comment = _CommentStub(status="ok")
    _patch_io(monkeypatch, comment=comment)
    monkeypatch.setattr(settings.neurocomment, "reply_delay_min_seconds", 1800.0)
    monkeypatch.setattr(settings.neurocomment, "reply_delay_max_seconds", 1800.0)

    async def _spend(seconds: float) -> None:
        # An honest wait: the clock moves by exactly what was asked for, and the sweep runs
        # while it does. Unsliced, the single 1800s wait puts the row twice past the cutoff.
        _advance_clock("@a", 10, seconds=seconds)
        await _sweep._reclaim_stale_claims(datetime.now(UTC))

    monkeypatch.setattr(engine.asyncio, "sleep", _spend)

    await engine.handle_new_post(NewPostEvent(channel="@a", post_id=10, text="hi there"))

    assert len(comment.posts) == 1
    row = await fetch_comment("@a", 10)
    assert row is not None
    assert row.status == "posted"


@pytest.mark.asyncio
async def test_the_send_is_abandoned_when_the_claim_is_no_longer_ours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A beat that finds no claim must stop the send, not merely fail to prevent it.

    Safe, and deliberately so: the reclaim marks ``failed`` rather than deleting, so nobody
    else can take this post — but it HAS handed the account's quota slot back, and the
    campaign now counts the attempt as a failure. Sending anyway would publish a comment
    charged to nobody, under a row saying it never happened.
    """
    await _make_campaign("@a", "acc-1")
    comment = _CommentStub(status="ok")
    _patch_io(monkeypatch, comment=comment)
    monkeypatch.setattr(settings.neurocomment, "reply_delay_min_seconds", 120.0)
    monkeypatch.setattr(settings.neurocomment, "reply_delay_max_seconds", 120.0)

    async def _misfire(_seconds: float) -> None:
        # A cutoff that catches even a beaten claim: a misfire, or the crash-between-send-
        # and-commit it cannot be told apart from.
        await _sweep._reclaim_stale_claims(datetime.now(UTC) + timedelta(seconds=1000))

    monkeypatch.setattr(engine.asyncio, "sleep", _misfire)

    await engine.handle_new_post(NewPostEvent(channel="@a", post_id=10, text="hi there"))

    assert comment.posts == []
    row = await fetch_comment("@a", 10)
    assert row is not None
    assert row.status == "failed"
    assert row.comment_msg_id is None  # nothing was published, so there is no id to keep
    logs = await list_recent_logs(limit=50)
    assert [e for e in logs if e.event == "neurocomment_claim_lost_before_send"]


class _RowLosingComment(_CommentStub):
    """The claim row disappears while the send is in flight — the third commit path.

    Only ``release_claim`` deletes a row and only this worker calls it, so the real thing
    should be unreachable; the branch exists because if it ever happens a live comment is
    under the post with nothing at all recording it.
    """

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        await release_claim("@a", 10)
        return await super().execute(account_id, action)


@pytest.mark.asyncio
async def test_a_delivery_onto_a_vanished_row_is_reported_as_the_error_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly one line per delivery, on the third path too — and the RIGHT line.

    Without the ``None`` check the missing row reaches ``record.status`` instead, and the
    blanket handler downgrades it to ``neurocomment_post_commit_failed`` — a generic commit
    error for the one case where a comment is live and completely unrecorded.
    """
    await _make_campaign("@a", "acc-1")
    comment = _RowLosingComment(status="ok")
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@a", post_id=10, text="hi there"))

    assert len(comment.posts) == 1  # the comment IS live under the post
    assert await fetch_comment("@a", 10) is None  # ... and nothing records it
    events = [e.event for e in await list_recent_logs(limit=50)]
    assert "neurocomment_posted_row_missing" in events
    assert "neurocomment_post_commit_failed" not in events


class _ClaimLosingGen(_GenStub):
    """The claim is reclaimed during the first round, which is then rejected on word count.

    So round two's beat is the first thing to find the claim gone — exactly the case the
    in-loop beat used to notice and throw away.
    """

    def __init__(self) -> None:
        super().__init__("word " * 99, "a nice comment")  # too_long, then acceptable

    async def generate_text(self, _request: object) -> GeminiResult:
        if self.calls == 0:
            # A cutoff that catches even a beaten claim: a misfire, or the crash-between-
            # send-and-commit it cannot be told apart from.
            await _sweep._reclaim_stale_claims(datetime.now(UTC) + timedelta(seconds=1000))
        return await super().generate_text(_request)


@pytest.mark.asyncio
async def test_a_claim_lost_mid_ladder_stops_paying_for_more_generations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The in-loop beat's answer is acted on, because the pre-send gate will abandon anyway.

    Every Gemini call after the claim goes is guaranteed waste — up to three rounds of six
    paid attempts, ~735s of wall clock — spent on a comment that cannot be sent. Reported
    as an exhaustion ``reason``, which is what that field is for.
    """
    await _make_campaign("@a", "acc-1")
    comment = _CommentStub(status="ok")
    gen = _ClaimLosingGen()
    _patch_io(monkeypatch, comment=comment, gen=gen)

    await engine.handle_new_post(NewPostEvent(channel="@a", post_id=10, text="hi there"))

    assert gen.calls == 1  # round two never pays Gemini, because round two cannot send
    assert comment.posts == []
    row = await fetch_comment("@a", 10)
    assert row is not None
    assert row.status == "failed"
    logs = await list_recent_logs(limit=50)
    exhausted = [e for e in logs if e.event == "neurocomment_generation_exhausted"]
    assert [e.extra.get("reason") for e in exhausted] == ["claim_lost"]


@pytest.mark.asyncio
async def test_a_beaten_claim_is_not_reclaimed_however_old_the_row_is() -> None:
    """Pins the stamp the reclaim actually reads, which nothing else discriminates.

    The two sweep/lifecycle tests adapted for this age BOTH stamps, so they stay green with
    ``updated_at`` reverted to ``created_at`` — they prove the reclaim still works, not that
    it reads the beat.
    """
    campaign_id = await _make_campaign("@a", "acc-1")
    assert await claim_comment("@a", 10, campaign_id, "acc-1") is True
    ancient = (datetime.now(UTC) - timedelta(hours=6)).isoformat()
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_comments SET created_at = ? WHERE channel = ? AND post_id = ?",
            (ancient, "@a", 10),
        )
    assert await touch_comment_claim("@a", 10) is True  # the worker is alive and says so

    await _sweep._reclaim_stale_claims(datetime.now(UTC))

    row = await fetch_comment("@a", 10)
    assert row is not None
    assert row.status == "claimed"
    assert row.created_at == ancient  # ... on a row six hours old


@pytest.mark.asyncio
async def test_the_deletion_stamp_never_touches_a_claim_still_in_flight() -> None:
    """A row carrying an id but still ``claimed`` must be left completely alone.

    Reachable: the id write lands, then ``mark_comment_posted`` raises or the process dies,
    so the row keeps ``claimed`` WITH an id — and the sweep's scan set is exactly "carries an
    id". Stamping it would bump ``updated_at``, deferring the stale-claim reclaim by another
    whole cutoff, and leave the nonsense state ``claimed`` AND ``deleted_at``, which the
    retention prune (it excludes ``claimed``) never cleans up.
    """
    campaign_id = await _make_campaign("@a", "acc-1")
    assert await claim_comment("@a", 10, campaign_id, "acc-1") is True
    await record_comment_msg_id("@a", 10, 555)

    marked = await mark_comments_deleted("@a", [555])

    assert marked.comments == []
    row = await fetch_comment("@a", 10)
    assert row is not None
    assert (row.status, row.deleted_at) == ("claimed", None)
