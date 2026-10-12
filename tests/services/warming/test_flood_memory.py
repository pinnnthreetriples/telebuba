"""Flood memory: spam blocks are remembered, and a quarantine release ramps back in."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    create_account,
    fetch_warming_state,
    save_warming_settings,
    upsert_warming_state,
)
from core.repositories.warming_floods import (
    fetch_warming_flood_summary,
    list_warming_flood_summaries,
    record_warming_flood_event,
)
from schemas.accounts import AccountCreate
from schemas.warming import WarmingStateWrite
from schemas.warming_flood import WarmingFloodEventWrite
from services import warming
from services.warming import _flood_memory, _seams
from services.warming._phases import compute_intensity, recovery_limits
from tests.services.warming._support import (
    _no_quiet_days,
    _Recorder,
    _seed_channel,
    _set_settings,
    _verdict,
)

if TYPE_CHECKING:
    from schemas.spam_status import SpamStatusVerdict

_NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
_AGED_HOURS = 24.0 * 90  # past every phase bound, so the phase cap is "warmed"


async def _run_cycle_with(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_quiet_days(monkeypatch)
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")
    await create_account(AccountCreate(account_id="acc-1"))
    await warming.run_loop_iteration("acc-1")


@pytest.mark.asyncio
async def test_a_peer_flood_cycle_is_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    recorder.peer_flood_on.add("join_channel")

    await _run_cycle_with(recorder, monkeypatch)

    summary = await fetch_warming_flood_summary("acc-1")
    assert summary.flood_total == 1
    assert summary.last_flood_at is not None


@pytest.mark.asyncio
async def test_a_broken_flood_history_still_quarantines(monkeypatch: pytest.MonkeyPatch) -> None:
    # The history row is best-effort: a DB fault there must not turn the peer-flood
    # quarantine into an error state.
    async def explode(_write: WarmingFloodEventWrite) -> None:
        msg = "history down"
        raise RuntimeError(msg)

    monkeypatch.setattr(_flood_memory, "record_warming_flood_event", explode)
    recorder = _Recorder()
    recorder.peer_flood_on.add("join_channel")

    await _run_cycle_with(recorder, monkeypatch)

    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "quarantine"


@pytest.mark.asyncio
async def test_a_flood_wait_cycle_is_not_a_spam_block(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    recorder.flood_on.add("join_channel")

    await _run_cycle_with(recorder, monkeypatch)

    assert (await fetch_warming_flood_summary("acc-1")).flood_total == 0


@pytest.mark.asyncio
async def test_a_clean_cycle_is_not_a_spam_block(monkeypatch: pytest.MonkeyPatch) -> None:
    await _run_cycle_with(_Recorder(), monkeypatch)

    assert (await fetch_warming_flood_summary("acc-1")).flood_total == 0


@pytest.mark.asyncio
async def test_summaries_count_per_account_and_keep_the_latest() -> None:
    for account_id, created_at in (
        ("acc-1", "2026-10-01T00:00:00+00:00"),
        ("acc-1", "2026-10-05T00:00:00+00:00"),
        ("acc-2", "2026-10-03T00:00:00+00:00"),
    ):
        await record_warming_flood_event(
            WarmingFloodEventWrite(account_id=account_id, created_at=created_at),
        )

    summaries = await list_warming_flood_summaries()

    assert summaries["acc-1"].flood_total == 2
    assert summaries["acc-1"].last_flood_at == "2026-10-05T00:00:00+00:00"
    assert summaries["acc-2"].flood_total == 1
    assert (await fetch_warming_flood_summary("acc-3")).flood_total == 0


@pytest.mark.asyncio
async def test_a_clean_release_stamps_recovered_at(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="quarantine"))

    async def fake_refresh(account_id: str, *, force: bool = False) -> SpamStatusVerdict:  # noqa: ARG001
        return _verdict(account_id, "clean")

    monkeypatch.setattr(_seams, "refresh_spam_status", fake_refresh)
    before = datetime.now(UTC)

    await warming.run_loop_iteration("acc-1")

    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.recovered_at is not None
    assert datetime.fromisoformat(state.recovered_at) >= before


@pytest.mark.parametrize(
    ("days", "factor"),
    [(0.0, 0.3), (1.0, 0.44), (2.5, 0.65), (5.0, 1.0), (20.0, 1.0)],
)
def test_the_limit_ramps_from_30_to_100_percent_over_five_days(days: float, factor: float) -> None:
    released = (_NOW - timedelta(days=days)).isoformat()

    assert recovery_limits(released, _NOW)[0] == pytest.approx(factor)


def test_a_never_released_account_is_unrestricted() -> None:
    assert recovery_limits(None, _NOW) == (1.0, None)
    assert recovery_limits("not a date", _NOW) == (1.0, None)


def test_dms_stay_closed_for_fourteen_days_after_release() -> None:
    released = _NOW - timedelta(days=13)
    started = (_NOW - timedelta(days=60)).isoformat()
    blocked = compute_intensity(
        _AGED_HOURS, "excellent", recovered_at=released.isoformat(), now=_NOW, started_at=started
    )
    later = _NOW + timedelta(days=1, seconds=1)
    reopened = compute_intensity(
        _AGED_HOURS, "excellent", recovered_at=released.isoformat(), now=later, started_at=started
    )

    assert blocked.dm_allowed is False
    assert blocked.dm_blocked_until == (released + timedelta(days=14)).isoformat()
    assert reopened.dm_allowed is True
    assert reopened.dm_blocked_until is None


def test_the_ramp_scales_the_phase_cap() -> None:
    full = compute_intensity(_AGED_HOURS, "excellent", now=_NOW)
    ramped = compute_intensity(_AGED_HOURS, "excellent", recovered_at=_NOW.isoformat(), now=_NOW)

    assert full.limit_factor == 1.0
    assert ramped.limit_factor == pytest.approx(0.3)
    assert ramped.daily_cap == max(1, round(full.daily_cap * 0.3))


@pytest.mark.asyncio
async def test_the_iteration_parks_on_the_ramped_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_quiet_days(monkeypatch)
    monkeypatch.setattr(_seams, "execute", _Recorder().execute)
    await _seed_channel()
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))
    now = datetime.now(UTC)
    ramped_cap = compute_intensity(0.0, recovered_at=now.isoformat(), now=now).daily_cap
    assert ramped_cap < settings.warming.phase_daily_cap["intro"]
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            daily_actions=ramped_cap,
            daily_count_date=now.date().isoformat(),
            recovered_at=now.isoformat(),
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    assert result.detail == "daily limit"


@pytest.mark.asyncio
async def test_the_board_card_carries_the_flood_memory() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    released = datetime.now(UTC) - timedelta(days=1)
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="sleeping", recovered_at=released.isoformat()),
    )
    for _ in range(2):
        await record_warming_flood_event(
            WarmingFloodEventWrite(account_id="acc-1", created_at=released.isoformat()),
        )

    card = (await warming.load_board()).warming[0]

    assert card.flood_total == 2
    assert card.dm_blocked_until == (released + timedelta(days=14)).isoformat()
    assert card.limit_factor == pytest.approx(0.44)
    assert card.dm_allowed is False
    assert "floods x2" in card.trust_reasons


@pytest.mark.asyncio
async def test_a_clean_board_card_has_no_flood_memory() -> None:
    await create_account(AccountCreate(account_id="acc-1"))

    card = (await warming.load_board()).idle[0]

    assert (card.flood_total, card.dm_blocked_until, card.limit_factor) == (0, None, 1.0)
