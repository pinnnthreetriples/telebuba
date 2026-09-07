"""Media and Premium extras — byte budget, premium gate, action shapes, cycle threading."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from schemas.telegram_actions_warming import WarmConsumeMedia, WarmEmojiStatus
from schemas.warming import WarmingCycleRequest
from services import warming
from services.warming import _cycle, _extras, _extras_media, _seams
from services.warming._extras import EXTRAS, _is_eligible, run_extras_step
from services.warming._extras_ctx import MEDIA_MIN_BYTES
from services.warming._steps import _ChannelTally
from tests.services.warming._support import _Recorder, _seed_ready_account, _set_settings
from tests.services.warming.test_extras_step import _PREMIUM, _RECENT_IDS, _ctx, _extras_range
from tests.services.warming.test_extras_writes import _MIXED_IDS, _one

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.telegram_actions import ActionResult, TelegramAction
    from services.warming._extras_ctx import _ExtraContext

    _Runner = Callable[[_ExtraContext], Awaitable[ActionResult]]

_MEDIA_SPECS = tuple(s for s in EXTRAS if s.run.__module__ == _extras_media.__name__)
_BY_KEY = {s.key: s for s in EXTRAS}
_PER_ITEM = 3_000_000


def _media_ctx(
    bytes_left: int,
    *,
    recent_ids: dict[str, list[int]] | None = None,
    tally: _ChannelTally | None = None,
) -> _ExtraContext:
    """A Premium account with posts read and ``bytes_left`` of media budget."""
    return _ctx(
        recent_ids=_RECENT_IDS if recent_ids is None else recent_ids,
        account=_PREMIUM,
        media_bytes_left=bytes_left,
        tally=tally,
    )


# --- eligibility ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("premium", "expected"),
    [(True, True), (False, False), (None, False)],
    ids=["premium", "not_premium", "unknown"],
)
def test_emoji_status_needs_a_known_premium_account(
    *, premium: bool | None, expected: bool
) -> None:
    account = _PREMIUM.model_copy(update={"premium": premium})
    assert _is_eligible(_BY_KEY["emoji_status"], _ctx(account=account)) is expected


def test_emoji_status_is_ineligible_without_an_account_row() -> None:
    assert _is_eligible(_BY_KEY["emoji_status"], _ctx(media_bytes_left=10**9)) is False


@pytest.mark.parametrize(
    ("bytes_left", "expected"),
    [(0, False), (MEDIA_MIN_BYTES - 1, False), (MEDIA_MIN_BYTES, True)],
    ids=["disabled", "just_under_floor", "at_floor"],
)
@pytest.mark.parametrize("key", ["video", "voice"])
def test_media_extras_need_at_least_the_schema_floor(
    key: str, bytes_left: int, *, expected: bool
) -> None:
    assert _is_eligible(_BY_KEY[key], _media_ctx(bytes_left)) is expected
    # Bytes alone are not enough: there must be a post to consume.
    assert _is_eligible(_BY_KEY[key], _ctx(media_bytes_left=bytes_left)) is False


# --- byte budget ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bytes_left", "expected_max"),
    [(_PER_ITEM + 1, _PER_ITEM), (_PER_ITEM - 1, _PER_ITEM - 1)],
    ids=["capped_by_item", "capped_by_cycle"],
)
@pytest.mark.parametrize("runner", [_extras_media.video, _extras_media.voice])
@pytest.mark.asyncio
async def test_media_debits_min_of_item_cap_and_budget_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    runner: _Runner,
    bytes_left: int,
    expected_max: int,
) -> None:
    monkeypatch.setattr(settings.warming, "extras_media_bytes_per_item", _PER_ITEM)
    ctx = _media_ctx(bytes_left)
    recorder = _Recorder()
    seen: list[int] = []

    async def _execute(account_id: str, action: TelegramAction) -> ActionResult:
        seen.append(ctx.media_bytes_left)  # already debited when the dispatcher sees it
        return await recorder.execute(account_id, action)

    monkeypatch.setattr(_seams, "execute", _execute)

    await runner(ctx)

    action = _one(recorder)
    assert isinstance(action, WarmConsumeMedia)
    assert action.max_bytes == expected_max
    assert seen == [bytes_left - expected_max]
    assert ctx.tally.attempts == 1


@pytest.mark.parametrize("flood", ["flood_on", "peer_flood_on"])
@pytest.mark.asyncio
async def test_media_bytes_stay_spent_when_the_dispatch_fails(
    monkeypatch: pytest.MonkeyPatch, flood: str
) -> None:
    recorder = _Recorder()
    setattr(recorder, flood, {"warm_consume_media"})
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    ctx = _media_ctx(_PER_ITEM + 1)

    result = await _extras_media.video(ctx)

    assert result is not None
    assert result.status != "ok"
    assert ctx.media_bytes_left == 1


@pytest.mark.asyncio
async def test_second_media_draw_sees_the_reduced_budget_then_nothing_is_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_extras, "EXTRAS", (_BY_KEY["video"], _BY_KEY["voice"]))
    _extras_range(monkeypatch, 2, 2)
    monkeypatch.setattr(settings.warming, "extras_media_bytes_per_item", _PER_ITEM)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    ctx = _media_ctx(_PER_ITEM + MEDIA_MIN_BYTES)

    landed = await run_extras_step(ctx)

    assert landed is True
    drawn = [a.max_bytes for _id, a in recorder.actions if isinstance(a, WarmConsumeMedia)]
    assert sorted(drawn) == [MEDIA_MIN_BYTES, _PER_ITEM]
    assert ctx.media_bytes_left == 0
    # The budget is gone: a further draw in the same cycle is ineligible, not clamped.
    assert not any(_is_eligible(s, ctx) for s in _MEDIA_SPECS if "media_bytes" in s.needs)


@pytest.mark.asyncio
async def test_second_media_draw_under_the_action_floor_is_skipped_not_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eligibility is judged once per draw; the first item can leave less than the floor."""
    monkeypatch.setattr(_extras, "EXTRAS", (_BY_KEY["video"], _BY_KEY["voice"]))
    _extras_range(monkeypatch, 2, 2)
    monkeypatch.setattr(settings.warming, "extras_media_bytes_per_item", _PER_ITEM)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    ctx = _media_ctx(_PER_ITEM + MEDIA_MIN_BYTES - 1)

    landed = await run_extras_step(ctx)  # would raise ValidationError without the guard

    assert landed is True
    (action,) = (a for _id, a in recorder.actions)
    assert isinstance(action, WarmConsumeMedia)
    assert action.max_bytes == _PER_ITEM
    assert ctx.media_bytes_left == MEDIA_MIN_BYTES - 1
    assert ctx.tally.attempts == 1


@pytest.mark.parametrize(
    ("bytes_left", "flood", "pauses"),
    [
        (_PER_ITEM, set(), 1),
        (_PER_ITEM, {"warm_consume_media"}, 0),
        (MEDIA_MIN_BYTES - 1, set(), 0),
    ],
    ids=["ok_pauses", "flood_does_not", "skipped_does_not"],
)
@pytest.mark.asyncio
async def test_media_pauses_after_a_landed_download_only(
    monkeypatch: pytest.MonkeyPatch, bytes_left: int, flood: set[str], pauses: int
) -> None:
    recorder = _Recorder()
    recorder.flood_on = flood

    async def played(account_id: str, action: TelegramAction) -> ActionResult:
        # A landed download reports the post it played; a skip has no ``message_id``.
        result = await recorder.execute(account_id, action)
        return result.model_copy(update={"message_id": 7}) if result.status == "ok" else result

    monkeypatch.setattr(_seams, "execute", played)
    monkeypatch.setattr(settings.warming, "extras_media_pause_seconds", (5.0, 30.0))
    seen: list[tuple[float, float]] = []

    async def pause(lo: float, hi: float) -> None:
        seen.append((lo, hi))

    monkeypatch.setattr(_extras_media, "_human_pause", pause)

    await _extras_media.video(_media_ctx(bytes_left))

    assert seen == [(5.0, 30.0)] * pauses


@pytest.mark.asyncio
async def test_an_ok_row_that_played_nothing_does_not_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A skip (``no_media``, ``download_stalled``...) is ``ok`` without a ``message_id``."""
    recorder = _Recorder()  # its ok result carries no message_id — exactly a skip row
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    seen: list[tuple[float, float]] = []

    async def pause(lo: float, hi: float) -> None:
        seen.append((lo, hi))

    monkeypatch.setattr(_extras_media, "_human_pause", pause)

    await _extras_media.video(_media_ctx(_PER_ITEM))

    assert recorder.types() == ["warm_consume_media"]
    assert seen == []


# --- action shapes -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("runner", "kind"), [(_extras_media.video, "video"), (_extras_media.voice, "voice")]
)
@pytest.mark.asyncio
async def test_media_consumes_only_posts_the_account_read(
    monkeypatch: pytest.MonkeyPatch, runner: _Runner, kind: str
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)

    await runner(_media_ctx(10**7, recent_ids=_MIXED_IDS))

    action = _one(recorder)
    assert isinstance(action, WarmConsumeMedia)
    assert action.kind == kind
    assert action.channel == "full"
    assert action.message_ids == _MIXED_IDS["full"][:5]


@pytest.mark.parametrize(
    ("draw", "probability", "expected"), [(0.0, 0.3, True), (0.5, 0.3, False), (0.0, 0.0, False)]
)
@pytest.mark.asyncio
async def test_emoji_status_draws_lifetime_from_the_window_and_clears_by_probability(
    monkeypatch: pytest.MonkeyPatch, draw: float, probability: float, *, expected: bool
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(_seams.rng, "random", lambda: draw)
    monkeypatch.setattr(settings.warming, "extras_emoji_until_hours", (2.0, 3.0))
    monkeypatch.setattr(settings.warming, "extras_emoji_clear_probability", probability)
    tally = _ChannelTally()

    await _extras_media.emoji_status(_ctx(tally=tally, account=_PREMIUM))

    action = _one(recorder)
    assert isinstance(action, WarmEmojiStatus)
    assert 2.0 <= action.until_hours <= 3.0
    assert 0 <= action.status_index < _extras_media._EMOJI_STATUSES_MAX
    assert action.clear is expected
    assert tally.attempts == 1


@pytest.mark.parametrize(("premium", "dispatched"), [(True, 1), (False, 0), (None, 0)])
@pytest.mark.asyncio
async def test_step_sends_the_emoji_status_only_to_a_premium_account(
    monkeypatch: pytest.MonkeyPatch, *, premium: bool | None, dispatched: int
) -> None:
    monkeypatch.setattr(_extras, "EXTRAS", (_BY_KEY["emoji_status"],))
    _extras_range(monkeypatch, 1, 1)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    account = _PREMIUM.model_copy(update={"premium": premium})

    await run_extras_step(_ctx(account=account))

    assert len(recorder.actions) == dispatched


# --- registry -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_media_spec_is_a_write_that_books_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert {s.key for s in _MEDIA_SPECS} == {"video", "voice", "emoji_status"}
    assert all(s.kind == "write" for s in _MEDIA_SPECS)
    assert _BY_KEY["video"].needs == _BY_KEY["voice"].needs == {"recent_ids", "media_bytes"}
    assert _BY_KEY["emoji_status"].needs == {"premium"}
    monkeypatch.setattr(_extras, "EXTRAS", _MEDIA_SPECS)
    _extras_range(monkeypatch, 20, 20)
    tally = _ChannelTally()
    seen: list[int] = []

    async def _execute(account_id: str, action: TelegramAction) -> ActionResult:
        seen.append(tally.attempts)  # already booked when the dispatcher sees it
        return await _Recorder().execute(account_id, action)

    monkeypatch.setattr(_seams, "execute", _execute)

    landed = await run_extras_step(_media_ctx(10**7, tally=tally))

    assert landed is True
    assert seen == [1, 2, 3]
    assert tally.extras == len(_MEDIA_SPECS)


# --- the cycle threads the account and the byte budget ---------------------------------


@pytest.mark.asyncio
async def test_cycle_hands_the_account_row_and_the_configured_byte_budget_to_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_seams, "execute", _Recorder().execute)
    monkeypatch.setattr(settings.warming, "extras_media_bytes_per_cycle", 4_321_000)
    await _seed_ready_account()
    await _set_settings(chat=False, reactions=False, key="")
    contexts: list[_ExtraContext] = []

    async def _spy(ctx: _ExtraContext) -> bool:
        contexts.append(ctx)
        return False

    monkeypatch.setattr(_cycle, "run_extras_step", _spy)

    await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    (ctx,) = contexts
    assert ctx.account is not None
    assert (ctx.account.account_id, ctx.account.premium) == ("acc-1", None)
    assert ctx.media_bytes_left == 4_321_000
