"""Chat extras — leave policy, archive/mute draws, poll ids, eligibility, re-join cooldown."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import update

from core.config import settings
from core.db import (
    _get_engine,
    _warming_joined_channels,
    is_channel_joined,
    list_joined_channels,
    record_channel_joined,
    record_channel_left,
)
from schemas._warming_extras import JoinedChannel
from schemas.telegram_actions import LeaveChannel
from schemas.telegram_actions_warming import WarmMutePeer, WarmToggleArchive, WarmVoteInPoll
from schemas.warming import WarmingChannel, WarmingCycleRequest
from services import warming
from services.warming import _cycle, _extras, _extras_chats, _seams
from services.warming._extras import EXTRAS, _is_eligible, run_extras_step
from services.warming._steps import _ChannelTally
from tests.services.warming._support import _Recorder, _seed_channel, _set_settings
from tests.services.warming.test_extras_step import _ctx, _extras_range
from tests.services.warming.test_extras_writes import _one

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.telegram_actions import ActionResult, TelegramAction
    from services.warming._extras_ctx import _ExtraContext

_NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)
_SECOND = timedelta(seconds=1)
_CHAT_SPECS = tuple(s for s in EXTRAS if s.run.__module__ == _extras_chats.__name__)
_BY_KEY = {s.key: s for s in EXTRAS}
# The seeded ``choice`` over two items picks index 1: put the excluded row first so an
# unfiltered draw would land on it and fail the assertion.
_LEFT_THEN_JOINED = [
    JoinedChannel(channel="gone", created_at=_NOW.isoformat(), left_at=_NOW.isoformat()),
    JoinedChannel(channel="here", created_at=_NOW.isoformat()),
]


def _joined(channel: str, *, age: timedelta, left: timedelta | None = None) -> JoinedChannel:
    return JoinedChannel(
        channel=channel,
        created_at=(_NOW - age).isoformat(),
        left_at=None if left is None else (_NOW - left).isoformat(),
    )


def _channel(name: str) -> WarmingChannel:
    return WarmingChannel(channel=name, created_at="2026-01-01T00:00:00+00:00")


# --- leave policy ------------------------------------------------------------


def test_leave_candidates_need_exactly_the_configured_age(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "extras_leave_min_age_days", 3)
    exact = _joined("exact", age=timedelta(days=3))
    fresh = _joined("fresh", age=timedelta(days=3) - _SECOND)
    assert _extras_chats._leave_candidates(_ctx(joined=[exact, fresh]), _NOW) == ["exact"]


@pytest.mark.parametrize(
    ("excluded", "chosen"),
    [
        (_joined("reading", age=timedelta(days=30)), [_channel("reading")]),
        (_joined("+AbCdEfGh12", age=timedelta(days=30)), []),
        (_joined("gone", age=timedelta(days=30), left=timedelta(days=30)), []),
    ],
    ids=["read this cycle", "private invite", "already left"],
)
def test_leave_candidates_skip_chosen_invites_and_left_rows(
    excluded: JoinedChannel, chosen: list[WarmingChannel]
) -> None:
    ok = _joined("ok", age=timedelta(days=30))
    ctx = _ctx(joined=[excluded, ok], chosen=chosen)
    assert _extras_chats._leave_candidates(ctx, _NOW) == ["ok"]


def test_leave_candidates_rest_for_the_configured_interval_after_a_leave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One leave per interval, measured from the newest ``left_at`` — no extra state."""
    monkeypatch.setattr(settings.warming, "extras_leave_min_interval_days", 3)
    ok = _joined("ok", age=timedelta(days=30))
    resting = _joined("gone", age=timedelta(days=30), left=timedelta(days=3) - _SECOND)
    rested = _joined("gone", age=timedelta(days=30), left=timedelta(days=3))
    assert _extras_chats._leave_candidates(_ctx(joined=[ok, resting]), _NOW) == []
    assert _extras_chats._leave_candidates(_ctx(joined=[ok, rested]), _NOW) == ["ok"]


@pytest.mark.parametrize("flooded", [False, True], ids=["ok", "flood_wait"])
@pytest.mark.asyncio
async def test_leave_books_dispatches_and_records_the_leave_only_on_ok(
    monkeypatch: pytest.MonkeyPatch, *, flooded: bool
) -> None:
    recorder = _Recorder()
    recorder.flood_on = {"leave_channel"} if flooded else set()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await record_channel_joined("acc-1", "old")
    tally = _ChannelTally()

    result = await _extras_chats.leave(
        _ctx(joined=[_joined("old", age=timedelta(days=30))], tally=tally)
    )

    action = _one(recorder)
    assert isinstance(action, LeaveChannel)
    assert action.channel == "old"
    assert tally.attempts == 1
    assert result is not None
    assert result.status == ("flood_wait" if flooded else "ok")
    assert await is_channel_joined("acc-1", "old") is flooded


@pytest.mark.asyncio
async def test_leave_is_not_applicable_without_a_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    tally = _ChannelTally()

    result = await _extras_chats.leave(
        _ctx(joined=[_joined("fresh", age=timedelta(days=1))], tally=tally)
    )

    assert result is None
    assert recorder.actions == []
    assert tally.attempts == 0


# --- archive / mute / polls --------------------------------------------------


@pytest.mark.parametrize(
    ("draw", "probability", "archived"), [(0.0, 0.5, True), (0.7, 0.5, False), (0.0, 0.0, False)]
)
@pytest.mark.asyncio
async def test_archive_picks_a_joined_channel_and_draws_the_direction(
    monkeypatch: pytest.MonkeyPatch, draw: float, probability: float, *, archived: bool
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(_seams.rng, "random", lambda: draw)
    monkeypatch.setattr(settings.warming, "extras_archive_probability", probability)
    tally = _ChannelTally()

    await _extras_chats.archive(_ctx(joined=_LEFT_THEN_JOINED, tally=tally))

    action = _one(recorder)
    assert isinstance(action, WarmToggleArchive)
    assert action.channel == "here"
    assert action.archived is archived
    assert tally.attempts == 1


@pytest.mark.asyncio
async def test_mute_picks_a_joined_channel_and_draws_hours_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "extras_mute_hours", [5.0])
    tally = _ChannelTally()

    await _extras_chats.mute(_ctx(joined=_LEFT_THEN_JOINED, tally=tally))

    action = _one(recorder)
    assert isinstance(action, WarmMutePeer)
    assert action.channel == "here"
    assert action.mute_hours == 5.0
    assert tally.attempts == 1


@pytest.mark.parametrize(
    "run", [_extras_chats.archive, _extras_chats.mute], ids=["archive", "mute"]
)
@pytest.mark.asyncio
async def test_archive_and_mute_never_aim_at_a_private_invite(
    monkeypatch: pytest.MonkeyPatch, run: Callable[[_ExtraContext], Awaitable[ActionResult]]
) -> None:
    """``+HASH`` is no resolvable peer — an unfiltered draw would fail the write outright."""
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(_seams.rng, "choice", lambda seq: seq[0])
    invite = _joined("+AbCdEfGh12", age=timedelta(days=30))

    await run(_ctx(joined=[invite, _LEFT_THEN_JOINED[1]]))

    action = _one(recorder)
    assert isinstance(action, WarmToggleArchive | WarmMutePeer)
    assert action.channel == "here"


@pytest.mark.asyncio
async def test_polls_votes_only_among_posts_the_account_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    ids = {"full": list(range(1, 9)), "empty": []}
    tally = _ChannelTally()

    await _extras_chats.polls(_ctx(recent_ids=ids, tally=tally))

    action = _one(recorder)
    assert isinstance(action, WarmVoteInPoll)
    assert action.channel == "full"
    assert action.message_ids == ids["full"][:5]
    assert 0 <= action.option_index < _extras_chats._POLL_OPTIONS_MAX
    assert tally.attempts == 1


# --- eligibility and the step ------------------------------------------------


@pytest.mark.parametrize("key", ["leave", "archive", "mute"])
def test_joined_bound_extras_need_a_channel_still_joined(key: str) -> None:
    spec = _BY_KEY[key]
    assert _is_eligible(spec, _ctx()) is False
    invite = _joined("+AbCdEfGh12", age=timedelta(days=30))
    assert _is_eligible(spec, _ctx(joined=[_LEFT_THEN_JOINED[0]])) is False
    assert _is_eligible(spec, _ctx(joined=[invite])) is False
    assert _is_eligible(spec, _ctx(joined=[_LEFT_THEN_JOINED[1]])) is True
    assert _is_eligible(spec, _ctx(recent_ids={"c": [1]})) is False


@pytest.mark.asyncio
async def test_every_chat_spec_is_a_write_that_books_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert {s.key for s in _CHAT_SPECS} == {"polls", "leave", "archive", "mute"}
    assert all(s.kind == "write" for s in _CHAT_SPECS)
    monkeypatch.setattr(_extras, "EXTRAS", _CHAT_SPECS)
    _extras_range(monkeypatch, 20, 20)
    tally = _ChannelTally()
    seen: list[int] = []

    async def _execute(account_id: str, action: TelegramAction) -> ActionResult:
        seen.append(tally.attempts)  # already booked when the dispatcher sees it
        return await _Recorder().execute(account_id, action)

    monkeypatch.setattr(_seams, "execute", _execute)
    ctx = _ctx(tally=tally, recent_ids={"c1": [1]}, joined=[_joined("old", age=timedelta(days=30))])

    landed = await run_extras_step(ctx)

    assert landed is True
    assert seen == [1, 2, 3, 4]
    assert tally.extras == len(_CHAT_SPECS)


# --- the cycle's re-join cooldown --------------------------------------------


def test_without_cooling_down_uses_the_configured_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "extras_rejoin_cooldown_days", 3)
    chosen = [_channel("cooling"), _channel("lapsed"), _channel("joined"), _channel("new")]
    joined = [
        _joined("cooling", age=timedelta(days=30), left=timedelta(days=3) - _SECOND),
        _joined("lapsed", age=timedelta(days=30), left=timedelta(days=3)),
        _joined("joined", age=timedelta(days=30)),
    ]
    kept = _cycle._without_cooling_down(chosen, joined, _NOW)
    assert [c.channel for c in kept] == ["lapsed", "joined", "new"]


def _set_left_at(account_id: str, channel: str, left_at: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_warming_joined_channels)
            .where(
                (_warming_joined_channels.c.account_id == account_id)
                & (_warming_joined_channels.c.channel == channel),
            )
            .values(left_at=left_at),
        )


@pytest.mark.asyncio
async def test_cycle_leaves_a_recently_left_channel_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()  # @channel_one
    await _set_settings(chat=False, reactions=False, key="")
    await record_channel_joined("acc-1", "channel_one")
    await record_channel_left("acc-1", "channel_one")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "ok"
    assert recorder.types() == ["set_online", "set_online"]  # nothing read, joined or watched
    assert await is_channel_joined("acc-1", "channel_one") is False


@pytest.mark.asyncio
async def test_cycle_rejoins_a_channel_once_the_cooldown_lapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")
    await record_channel_joined("acc-1", "channel_one")
    lapsed = datetime.now(UTC) - timedelta(days=settings.warming.extras_rejoin_cooldown_days + 1)
    _set_left_at("acc-1", "channel_one", lapsed.isoformat())

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.channels_joined == 1
    assert "read_channel" in recorder.types()
    (row,) = await list_joined_channels("acc-1")
    assert row.left_at is None  # the join step's upsert re-joined it
    assert row.created_at > lapsed.isoformat()
