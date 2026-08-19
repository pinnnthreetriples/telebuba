"""Outcomes that must NOT be charged to the account: a kick, and the transient faults.

Three regressions, one theme — the classifier used to bill the pair (and burn the post)
for something the pair did not do:

* a kick arriving as ``UserNotParticipantError`` fell into the generic tail, which writes
  no readiness, so the pair stayed ``ready`` and never carried the sentinel
  ``_rejoin.review_access_lost`` reads;
* ``status="unavailable"`` (pool connect / socket / timeout) took the 1-hour peer-flood
  cooldown and a terminal ``failed`` claim;
* a Gemini 429 that exhausted generation took the same terminal ``failed`` claim.

The claim row is the load-bearing assertion, and neither terminal state will do. ``failed``
is terminal (``_mark_comment`` never re-transitions it) on a row ``claim_comment`` already
refuses to overwrite, so it burns the post for every account in the fleet, permanently —
but merely leaving it ``claimed`` still spends quota (``_quota`` counts ``claimed``
alongside ``posted``, and only process startup ages stale claims out), billing the account
a day-cap slot for 24 hours. So the transient paths RELEASE the row: it must be gone.

With ONE exception, the last section here: ``UNCONFIRMED_ERROR_TYPE`` is the half of
``unavailable`` where the request reached Telegram and only the answer was lost. Releasing
a row whose comment may be LIVE hands the post back to be commented on twice, so that half
keeps the ``failed`` the rest of the file argues against — and the tests below pin that it
costs the post without also costing the account.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    fetch_channel_paused_until,
    fetch_comment,
    fetch_readiness,
    list_recent_logs,
)
from core.telegram_client import UNCONFIRMED_ERROR_TYPE
from schemas.gemini import GeminiResult
from schemas.telegram_actions import NewPostEvent
from services.content import try_reserve_sent
from services.neurocomment import _rejoin, _seams, _state, engine
from tests.services.neurocomment.engine_support import (
    _CommentStub,
    _make_campaign,
    _patch_io,
)

if TYPE_CHECKING:
    from schemas.gemini import GeminiStatus

pytestmark = pytest.mark.usefixtures("isolate_engine")


async def _has_event(event: str) -> bool:
    return any(entry.event == event for entry in await list_recent_logs(limit=50))


async def _latest_extra(event: str, key: str) -> object | None:
    for entry in await list_recent_logs(limit=50):
        if entry.event == event:
            return entry.extra.get(key)
    return None


# --------------------------------------------------------------------------- #
# A kick reported as UserNotParticipantError is lost access, not an unknown error
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_user_not_participant_parks_pair_with_join_failed_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other name Telegram gives a kick must reach the same branch as ChannelPrivate."""
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="failed", error_type="UserNotParticipantError")
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert (readiness.joined, readiness.captcha_passed, readiness.ready) == (False, True, False)
    # The whole point of the sentinel: the re-join rule can now see this pair.
    assert _rejoin.access_lost(readiness) is True
    assert await _has_event("neurocomment_post_access_lost") is True
    # Not a gate and not a ban: no channel pause, no sticky ban, no bounded cooldown.
    assert await fetch_channel_paused_until("@chan") is None
    assert readiness.banned is False
    assert _state.in_cooldown("acc-1", datetime.now(UTC), "@chan") is False
    assert await try_reserve_sent("a nice comment") is True  # the reserved text was released


@pytest.mark.asyncio
async def test_user_not_participant_pair_is_not_reselected_for_the_next_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loop the generic tail left open: re-picked on the channel's very next post."""
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="failed", error_type="UserNotParticipantError")
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=1, text="hi"))
    assert len(comment.calls) == 1

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hi"))

    assert len(comment.calls) == 1
    # The same sentinel ``ChannelPrivateError`` writes, so the same reading: ``_rejoin`` has
    # budget left for this pair and the miss log says which of the readiness states it is.
    assert await _latest_extra("neurocomment_no_account_available", "reason") == "rejoining"


# --------------------------------------------------------------------------- #
# status="unavailable" is the gateway's fault, never the account's
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unavailable_does_not_cool_the_pair_or_burn_the_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pool/socket/timeout flap must cost neither an hour nor the post."""
    monkeypatch.setattr(settings.neurocomment, "peer_flood_cooldown_seconds", 3600)
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="unavailable", error_type="TelegramClientPoolError")
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    now = datetime.now(UTC)
    assert _state.in_cooldown("acc-1", now, "@chan") is False
    assert _state.in_cooldown("acc-1", now) is False
    # Released rather than terminally failed: the row is gone, so it neither burns the
    # post for the fleet nor keeps holding the account's day-cap slot.
    assert await fetch_comment("@chan", 10) is None
    # The pair is untouched — it did nothing wrong.
    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert (readiness.ready, readiness.banned) == (True, False)
    assert await fetch_channel_paused_until("@chan") is None
    assert await try_reserve_sent("a nice comment") is True  # the reserved text was released


@pytest.mark.asyncio
async def test_unavailable_is_logged_as_infrastructure_not_as_a_failed_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement (c): an outage must stay legible in the feed, under its own name."""
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="unavailable", error_type="TimeoutError")
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert await _has_event("neurocomment_post_unavailable") is True
    assert await _has_event("neurocomment_post_failed") is False
    assert await _latest_extra("neurocomment_post_unavailable", "error_type") == "TimeoutError"


@pytest.mark.asyncio
async def test_unavailable_leaves_the_channel_usable_for_the_next_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fleet-wide symptom: a flap used to park account+channel for an hour."""
    monkeypatch.setattr(settings.neurocomment, "peer_flood_cooldown_seconds", 3600)
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="unavailable", error_type="ConnectionError")
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=1, text="hi"))
    comment.status = "ok"
    comment.message_id = 7
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hi"))

    record = await fetch_comment("@chan", 2)
    assert record is not None
    assert record.status == "posted"


@pytest.mark.asyncio
async def test_unavailable_does_not_spend_the_channels_daily_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cost the left-behind claim was still charging: a third of the day, per flap.

    Quota counts ``claimed`` alongside ``posted``, and stale claims are only reclaimed at
    process startup — so a claim left behind mid-run held its slot for the whole 24-hour
    window. At the shipped cap of 3 that is a third of the account's day on that channel,
    for a comment the gateway never sent. Pinned at a cap of 1 so one flap is the whole day.
    """
    monkeypatch.setattr(settings.neurocomment, "max_comments_per_hour", 100)
    monkeypatch.setattr(settings.neurocomment, "max_comments_per_channel_per_day", 1)
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="unavailable", error_type="TelegramClientPoolError")
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=1, text="hi"))

    # (a) the released row is gone, so it holds no slot the moment the attempt ends.
    assert await fetch_comment("@chan", 1) is None
    comment.status = "ok"
    comment.message_id = 7
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hi"))
    posted = await fetch_comment("@chan", 2)
    assert posted is not None
    assert posted.status == "posted"
    # (b) releasing frees the slot, it does not hand out a spare: the one comment that
    # WAS delivered still fills the cap of 1, so the next post is refused as normal.
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=3, text="hi"))
    assert await _latest_extra("neurocomment_no_account_available", "reason") == "quota_day"


# --------------------------------------------------------------------------- #
# ...but only the half of it that proves nothing was sent may release the claim
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_an_unconfirmed_send_is_not_released_back_for_a_second_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The double-comment the release path opened: the send DID go out, the answer didn't.

    ``release_claim`` DELETEs the row, so the post became claimable again — and Telethon
    closes an updates gap by RE-DELIVERING the post it missed. The second delivery won a
    fresh claim and commented a second time under a post that may already carry our
    comment. A terminal row is the only thing that can stop it.
    """
    await _make_campaign("@chan", "acc-1", "acc-2")
    comment = _CommentStub(status="unavailable", error_type=UNCONFIRMED_ERROR_TYPE)
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "failed"

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert len(comment.posts) == 1


@pytest.mark.asyncio
async def test_an_unconfirmed_send_still_costs_the_pair_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Burning the POST is the price of the ambiguity; billing the ACCOUNT is not.

    The row goes terminal because the comment may be live, but the fault is still the
    gateway's — so no cooldown, no readiness write, no channel pause, and the reserved
    text goes back so a later regeneration is not filtered as its own duplicate.
    """
    monkeypatch.setattr(settings.neurocomment, "peer_flood_cooldown_seconds", 3600)
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="unavailable", error_type=UNCONFIRMED_ERROR_TYPE)
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    now = datetime.now(UTC)
    assert _state.in_cooldown("acc-1", now, "@chan") is False
    assert _state.in_cooldown("acc-1", now) is False
    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert (readiness.ready, readiness.banned) == (True, False)
    assert await fetch_channel_paused_until("@chan") is None
    assert await try_reserve_sent("a nice comment") is True
    assert await _has_event("neurocomment_post_unavailable") is True
    assert await _latest_extra("neurocomment_post_unavailable", "error_type") == (
        UNCONFIRMED_ERROR_TYPE
    )


@pytest.mark.asyncio
async def test_an_unconfirmed_send_frees_the_quota_slot_it_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No phantom slot: ``failed`` stops counting the moment the attempt ends.

    ``_quota`` counts ``claimed`` and ``posted``, so the terminal row costs the account
    nothing on the next post — the same accounting the DELETE bought, without handing the
    post back. Pinned at a day cap of 1 so one unconfirmed send would be the whole day.
    """
    monkeypatch.setattr(settings.neurocomment, "max_comments_per_hour", 100)
    monkeypatch.setattr(settings.neurocomment, "max_comments_per_channel_per_day", 1)
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="unavailable", error_type=UNCONFIRMED_ERROR_TYPE)
    _patch_io(monkeypatch, comment=comment)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=1, text="hi"))

    comment.status = "ok"
    comment.message_id = 7
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hi"))

    record = await fetch_comment("@chan", 2)
    assert record is not None
    assert record.status == "posted"


# --------------------------------------------------------------------------- #
# A Gemini 429 storm is transient too
# --------------------------------------------------------------------------- #


def _patch_gemini(monkeypatch: pytest.MonkeyPatch, status: GeminiStatus) -> None:
    async def _generate(_request: object) -> GeminiResult:
        return GeminiResult(status=status, text=None)

    monkeypatch.setattr(_seams, "generate_text", _generate)


@pytest.mark.asyncio
async def test_rate_limited_generation_releases_the_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 429 exhaustion is the gateway's state — it must not burn the post terminally."""
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="ok")
    _patch_io(monkeypatch, comment=comment)
    _patch_gemini(monkeypatch, "rate_limited")

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert comment.posts == []  # nothing was generated, so nothing was sent
    # Released, not left in flight: ``failed`` would be terminal, ``claimed`` would go on
    # spending quota until the next process start.
    assert await fetch_comment("@chan", 10) is None
    assert await _latest_extra("neurocomment_generation_exhausted", "reason") == (
        "gemini_rate_limited"
    )
    # Requirement (a): still no cooldown charged to the account for the gateway's 429.
    assert _state.in_cooldown("acc-1", datetime.now(UTC) + timedelta(seconds=1)) is False


@pytest.mark.asyncio
async def test_non_rate_limited_exhaustion_still_marks_the_claim_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The contrast that keeps the fix honest: a real generation failure is still terminal."""
    await _make_campaign("@chan", "acc-1")
    _patch_io(monkeypatch, comment=_CommentStub(status="ok"))
    _patch_gemini(monkeypatch, "error")

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "failed"
    assert await _latest_extra("neurocomment_generation_exhausted", "reason") == "gemini_error"


@pytest.mark.asyncio
async def test_rate_limited_generation_does_not_spend_the_channels_daily_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same accounting bug on the generation side: a 429 held a slot for 24 hours."""
    monkeypatch.setattr(settings.neurocomment, "max_comments_per_hour", 100)
    monkeypatch.setattr(settings.neurocomment, "max_comments_per_channel_per_day", 1)
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="ok")
    _patch_io(monkeypatch, comment=comment)
    _patch_gemini(monkeypatch, "rate_limited")

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=1, text="hi"))

    # (b) generation never produced a comment, so there is nothing to charge for.
    assert comment.posts == []
    assert await fetch_comment("@chan", 1) is None  # (a) the slot is free again
    _patch_io(monkeypatch, comment=comment)  # the 429 storm passes; generation works again
    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=2, text="hi"))

    record = await fetch_comment("@chan", 2)
    assert record is not None
    assert record.status == "posted"


@pytest.mark.asyncio
async def test_exhaustion_names_the_generator_and_quotes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reason code says "gemini" for both providers, so the log must say which one.

    A live day of DeepSeek timeouts reached the operator as "ошибка Gemini" with the
    gateway's own message dropped on the floor — unreadable twice over.
    """
    await _make_campaign("@chan", "acc-1")
    _patch_io(monkeypatch, comment=_CommentStub(status="ok"))
    monkeypatch.setattr(settings.deepseek, "api_key", "k")  # text post + a key = DeepSeek

    async def _generate(_request: object) -> GeminiResult:
        return GeminiResult(status="error", error="ReadTimeout: timed out")

    monkeypatch.setattr(_seams, "generate_text_deepseek", _generate)

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert await _latest_extra("neurocomment_generation_exhausted", "reason") == "gemini_error"
    assert await _latest_extra("neurocomment_generation_exhausted", "error_type") == (
        "deepseek: ReadTimeout: timed out"
    )
