"""Per-day warming totals: what a settled cycle adds, CAS fencing, retention, read window."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from core.config import settings
from core.db import upsert_warming_state
from core.repositories.warming_daily_stats import (
    add_warming_daily_stats,
    list_warming_daily_stats,
)
from schemas.warming import (
    WarmingCycleResult,
    WarmingState,
    WarmingStateWrite,
    WarmingStateWriteResult,
)
from schemas.warming_daily_stats import WarmingDailyStats
from services import warming
from services.warming import _loop, _purge, _seams, daily_stats
from services.warming._reservation import _Reservation
from services.warming.daily_stats import cycle_increment, load_daily_stats, stats_day
from tests.services.warming._support import _no_quiet_days, _Recorder, _seed_warming_account

if TYPE_CHECKING:
    from schemas.trust import TrustScore

_RUN = "generation-a"


def _result(**fields: object) -> WarmingCycleResult:
    base: dict[str, object] = {
        "account_id": "acc-1",
        "status": "ok",
        "channels_joined": 1,
        "channels_read": 3,
        "reactions_sent": 2,
        "messages_sent": 1,
        "failures": 0,
    }
    return WarmingCycleResult.model_validate({**base, **fields})


async def _finalize(result: WarmingCycleResult, *, run_id: str = _RUN) -> None:
    today = datetime.now(UTC).date().isoformat()
    await _loop._finalize_after_cycle(
        "acc-1",
        result,
        1.0,
        _Reservation(0, today, 10, "token"),
        (4, datetime.now(UTC) + timedelta(hours=12), "sleeping"),
        run_id=run_id,
    )


async def _today() -> list[WarmingDailyStats]:
    return await list_warming_daily_stats("acc-1", await stats_day("acc-1", datetime.now(UTC)))


def _utc_day(moment: datetime) -> str:
    return moment.date().isoformat()


def _zone(monkeypatch: pytest.MonkeyPatch, tz_name: str | None) -> None:
    async def account_tz(_account_id: str) -> str | None:
        return tz_name

    monkeypatch.setattr(daily_stats, "_account_tz", account_tz)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tz_name", "expected"),
    [("America/Mexico_City", "2026-10-10"), (None, "2026-10-11"), ("Not/AZone", "2026-10-11")],
)
async def test_stats_day_is_the_accounts_local_date(
    monkeypatch: pytest.MonkeyPatch, tz_name: str | None, expected: str
) -> None:
    # 01:00 UTC is still the previous evening in Mexico City; no zone means UTC.
    _zone(monkeypatch, tz_name)
    assert await stats_day("acc-1", datetime(2026, 10, 11, 1, tzinfo=UTC)) == expected


@pytest.mark.asyncio
async def test_a_cycle_lands_on_the_accounts_local_day(monkeypatch: pytest.MonkeyPatch) -> None:
    _zone(monkeypatch, "America/Mexico_City")

    await daily_stats.record_cycle_stats(_result(), "intro", datetime(2026, 10, 11, 1, tzinfo=UTC))

    rows = await list_warming_daily_stats("acc-1", "2000-01-01")
    assert [(row.day, row.cycles) for row in rows] == [("2026-10-10", 1)]


@pytest.mark.parametrize(
    ("status", "cycles", "floods"),
    [("ok", 1, 0), ("failed", 1, 0), ("flood_wait", 1, 1), ("peer_flood", 1, 1), ("skipped", 0, 0)],
)
def test_a_cycle_adds_its_counters_and_one_cycle_unless_skipped(
    status: str, cycles: int, floods: int
) -> None:
    increment = cycle_increment(_result(status=status, failures=2), day="2026-10-11", phase="intro")

    assert increment == WarmingDailyStats(
        account_id="acc-1",
        day="2026-10-11",
        cycles=cycles,
        reads=3,
        reactions=2,
        joins=1,
        dms=1,
        failures=2,
        floods=floods,
        phase="intro",
    )


@pytest.mark.asyncio
async def test_a_landed_cycle_is_counted_with_its_phase_and_trust() -> None:
    await _seed_warming_account(run_id=_RUN)

    await _finalize(_result())
    await _finalize(_result(status="flood_wait", channels_read=1, failures=1))

    [row] = await _today()
    assert (row.cycles, row.reads, row.reactions, row.joins, row.dms) == (2, 4, 4, 2, 2)
    assert (row.failures, row.floods) == (1, 1)
    assert row.phase is not None
    assert row.trust_score is not None
    assert row.trust_band is not None


@pytest.mark.asyncio
async def test_a_cycle_whose_generation_moved_on_before_the_settle_writes_nothing() -> None:
    await _seed_warming_account(run_id="generation-b")

    await _finalize(_result())

    assert await _today() == []


@pytest.mark.asyncio
async def test_a_cycle_whose_final_write_loses_the_cas_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_warming_account(run_id=_RUN)
    real_set_state = _loop._set_state

    async def restart_then_write(
        account_id: str,
        state: WarmingState,
        **kwargs: Any,
    ) -> WarmingStateWriteResult:
        # A restart mints a new generation between the settle's read and its write.
        await upsert_warming_state(
            WarmingStateWrite(account_id=account_id, state="active", run_id="generation-b"),
        )
        return await real_set_state(account_id, state, **kwargs)

    monkeypatch.setattr(_loop, "_set_state", restart_then_write)

    await _finalize(_result())

    assert await _today() == []


@pytest.mark.asyncio
async def test_a_stop_cancelling_the_settle_mid_write_still_counts_the_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The state already counted the cycle, so its day's row must not miss it."""
    await _seed_warming_account(run_id=_RUN)
    entered, release = asyncio.Event(), asyncio.Event()
    real_trust = daily_stats.account_trust_score

    async def slow_trust(account_id: str) -> TrustScore:
        entered.set()
        await release.wait()
        return await real_trust(account_id)

    monkeypatch.setattr(daily_stats, "account_trust_score", slow_trust)
    settle = asyncio.create_task(_finalize(_result()))
    await entered.wait()

    settle.cancel()
    with pytest.raises(asyncio.CancelledError):
        await settle
    release.set()
    await asyncio.gather(*daily_stats._DETACHED)

    [row] = await _today()
    assert row.cycles == 1


@pytest.mark.asyncio
async def test_a_failed_stats_write_is_logged_and_never_fails_the_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[tuple[str, object]] = []

    async def broken(_increment: WarmingDailyStats) -> None:
        raise RuntimeError

    async def capture(_level: str, code: str, **kwargs: object) -> None:
        logged.append((code, kwargs.get("extra")))

    monkeypatch.setattr(daily_stats, "add_warming_daily_stats", broken)
    monkeypatch.setattr(daily_stats, "log_event", capture)

    await daily_stats.record_cycle_stats(_result(), "intro")

    assert logged == [("warming_daily_stats_write_failed", {"error_type": "RuntimeError"})]


@pytest.mark.asyncio
async def test_a_whole_iteration_lands_on_todays_row(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_quiet_days(monkeypatch)
    monkeypatch.setattr(_seams, "execute", _Recorder().execute)
    await _seed_warming_account()

    result = await warming.run_loop_iteration("acc-1")

    [row] = await _today()
    assert row.cycles == 1
    assert (row.reads, row.joins) == (result.channels_read, result.channels_joined)


@pytest.mark.asyncio
async def test_the_retention_sweep_keeps_a_year_of_days(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "daily_stats_retention_days", 365.0)
    now = datetime.now(UTC)
    kept, dropped = _utc_day(now - timedelta(days=365)), _utc_day(now - timedelta(days=366))
    for day in (dropped, kept, _utc_day(now)):
        await add_warming_daily_stats(WarmingDailyStats(account_id="acc-1", day=day, cycles=1))

    await _purge.purge_stale_history()

    rows = await list_warming_daily_stats("acc-1", "2000-01-01")
    assert [row.day for row in rows] == [kept, _utc_day(now)]


@pytest.mark.asyncio
async def test_a_zero_window_disables_the_daily_stats_purge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.warming, "daily_stats_retention_days", 0.0)
    await add_warming_daily_stats(
        WarmingDailyStats(account_id="acc-1", day="2000-01-01", cycles=1),
    )

    await _purge.purge_stale_history()

    assert len(await list_warming_daily_stats("acc-1", "2000-01-01")) == 1


@pytest.mark.asyncio
async def test_the_read_covers_the_last_days_including_today() -> None:
    now = datetime(2026, 10, 11, 12, tzinfo=UTC)
    for day in ("2026-10-08", "2026-10-09", "2026-10-11"):
        await add_warming_daily_stats(WarmingDailyStats(account_id="acc-1", day=day, cycles=1))

    window = await load_daily_stats("acc-1", 3, now=now)

    assert [row.day for row in window.items] == ["2026-10-09", "2026-10-11"]
