"""The per-phase daily join cap of the warming channel walk (Warming 2.0, stage 1.8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from core.config import settings
from core.db import (
    _get_engine,
    _warming_joined_channels,
    load_warming_settings,
    record_channel_joined,
)
from schemas.warming import WarmingCycleRequest, WarmingPhase
from services.warming import _seams, _steps
from tests.services.warming._support import _affinity_pool, _Recorder, _set_settings

_ACCOUNT = "acc-1"


def _backdate_join(channel: str, hours_ago: float) -> None:
    created = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
    with _get_engine().begin() as connection:
        connection.execute(
            update(_warming_joined_channels)
            .where(
                (_warming_joined_channels.c.account_id == _ACCOUNT)
                & (_warming_joined_channels.c.channel == channel),
            )
            .values(created_at=created),
        )


async def _walk(
    channels: int,
    phase: WarmingPhase,
    remaining_actions: int | None = None,
) -> _steps._ChannelTally:
    await _set_settings(chat=False, reactions=False, key="")
    tally = _steps._ChannelTally()
    await _steps._run_channel_loop(
        WarmingCycleRequest(account_id=_ACCOUNT, remaining_actions=remaining_actions),
        tally,
        _affinity_pool(channels),
        await load_warming_settings(),
        0.0,
        phase=phase,
    )
    return tally


def _joined(recorder: _Recorder) -> list[str]:
    return [a.channel for _id, a in recorder.actions if a.action_type == "join_channel"]


def _read(recorder: _Recorder) -> list[str]:
    return [a.channel for _id, a in recorder.actions if a.action_type == "read_channel"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase", "cap"),
    [("intro", 1), ("settling", 1), ("warming", 2), ("active", 3), ("warmed", 4)],
)
async def test_a_cycle_joins_at_most_the_phase_cap(
    monkeypatch: pytest.MonkeyPatch, phase: WarmingPhase, cap: int
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)

    tally = await _walk(channels=6, phase=phase)

    assert settings.warming.phase_joins_per_day_max[phase] == cap
    assert len(_joined(recorder)) == cap
    assert tally.joined == cap
    # Channels over the cap are left alone entirely: neither joined nor read.
    assert _read(recorder) == _joined(recorder)


@pytest.mark.asyncio
async def test_joins_older_than_a_day_leave_the_rolling_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await record_channel_joined(_ACCOUNT, "old")
    _backdate_join("old", hours_ago=25)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)

    await _walk(channels=3, phase="intro")

    assert len(_joined(recorder)) == 1


@pytest.mark.asyncio
async def test_a_join_inside_the_window_uses_up_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 23h ago is yesterday on the calendar but still inside the rolling day.
    await record_channel_joined(_ACCOUNT, "recent")
    _backdate_join("recent", hours_ago=23)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)

    await _walk(channels=3, phase="intro")

    assert _joined(recorder) == []


@pytest.mark.asyncio
async def test_capped_joins_spend_no_budget_and_joined_channels_are_still_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # chan_0 and chan_2 are already joined (today, so the intro cap of 1 is spent);
    # chan_1 and chan_3 are new and must be skipped without booking an attempt.
    for channel in ("chan_0", "chan_2"):
        await record_channel_joined(_ACCOUNT, channel)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)

    tally = await _walk(channels=4, phase="intro", remaining_actions=10)

    assert _joined(recorder) == []
    assert _read(recorder) == ["chan_0", "chan_2"]
    assert tally.attempts == len(recorder.actions) == 2


@pytest.mark.asyncio
async def test_the_persisted_trust_aware_phase_lowers_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An old account the loop pinned to ``intro`` by trust gets the intro cap."""

    async def _state(_account_id: str) -> SimpleNamespace:
        return SimpleNamespace(current_phase="intro")

    monkeypatch.setattr(_steps, "fetch_warming_state", _state)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)

    await _walk(channels=6, phase="warmed")

    assert len(_joined(recorder)) == 1


@pytest.mark.asyncio
async def test_join_disabled_never_counts_or_joins(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(*_args: object) -> int:
        raise AssertionError

    monkeypatch.setattr(_steps, "count_joins_since", _boom)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _set_settings(chat=False, reactions=False, key="")
    secret = (await load_warming_settings()).model_copy(update={"join_enabled": False})

    await _steps._run_channel_loop(
        WarmingCycleRequest(account_id=_ACCOUNT),
        _steps._ChannelTally(),
        _affinity_pool(2),
        secret,
        0.0,
        phase="warmed",
    )

    assert _joined(recorder) == []
    assert _read(recorder) == ["chan_0", "chan_1"]
