"""Exact scheduling and lifecycle-event contracts after a warming cycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo
from types import SimpleNamespace

import pytest

from core.config import settings
from schemas.warming import WarmingCycleResult, WarmingStateRecord
from services.warming import _seams, _transitions


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> _FrozenDateTime:
        return cls(2026, 7, 17, 12, tzinfo=tz)


_NOW = _FrozenDateTime.now(UTC)


@pytest.mark.asyncio
async def test_flood_wait_applies_one_margin_draw_then_active_hours_shift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uniform_calls: list[tuple[float, float]] = []
    shifts: list[tuple[datetime, str | None, str]] = []

    def uniform(lower: float, upper: float) -> float:
        uniform_calls.append((lower, upper))
        return 0.25

    async def account_tz(_account_id: str) -> str:
        return "Europe/Istanbul"

    def shift(value: datetime, timezone: str | None, _rng: object, account_id: str) -> datetime:
        shifts.append((value, timezone, account_id))
        return value + timedelta(hours=2)

    monkeypatch.setattr(settings.warming, "flood_wait_margin_fraction", 0.5)
    monkeypatch.setattr(_seams.rng, "uniform", uniform)
    monkeypatch.setattr(_transitions, "_account_tz", account_tz)
    monkeypatch.setattr(_transitions, "_shift_to_active_hours", shift)
    monkeypatch.setattr(_transitions, "datetime", _FrozenDateTime)

    actions, next_run, state = await _transitions._calculate_next_run(
        "acc-1",
        WarmingCycleResult(
            account_id="acc-1",
            status="flood_wait",
            flood_wait_seconds=80,
            attempted_actions=3,
        ),
        "normal",
        40,
    )

    unshifted = _NOW + timedelta(seconds=100)
    assert (actions, state, next_run) == (3, "flood_wait", unshifted + timedelta(hours=2))
    assert uniform_calls == [(0, 0.5)]
    assert shifts == [(unshifted, "Europe/Istanbul", "acc-1")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected_state"),
    [
        (WarmingCycleResult(account_id="acc-1", status="ok"), "sleeping"),
        (
            WarmingCycleResult(
                account_id="acc-1",
                status="failed",
                last_failed_action="set_online",
            ),
            "sleeping",
        ),
        (WarmingCycleResult(account_id="acc-1", status="failed", failures=1), "error"),
    ],
)
async def test_non_flood_outcomes_use_one_persona_schedule(
    monkeypatch: pytest.MonkeyPatch,
    result: WarmingCycleResult,
    expected_state: str,
) -> None:
    schedules: list[tuple[str, int]] = []

    def schedule(persona: str, daily_cap: int, _rng: object) -> float:
        schedules.append((persona, daily_cap))
        return 600.0

    async def account_tz(_account_id: str) -> None:
        return None

    monkeypatch.setattr(_transitions, "persona_next_run_seconds", schedule)
    monkeypatch.setattr(_transitions, "_account_tz", account_tz)
    monkeypatch.setattr(_transitions, "_shift_to_active_hours", lambda value, *_args: value)
    monkeypatch.setattr(_transitions, "datetime", _FrozenDateTime)

    _, next_run, state = await _transitions._calculate_next_run("acc-1", result, "active", 17)

    assert state == expected_state
    assert next_run == _NOW + timedelta(minutes=10)
    assert schedules == [("active", 17)]


def _state_record(
    *, current_phase: str | None, phase_entered_at: str | None, cycles_completed: int = 4
) -> WarmingStateRecord:
    return WarmingStateRecord.model_validate(
        {
            "account_id": "acc-1",
            "state": "sleeping",
            "updated_at": "2026-07-17T12:00:00+00:00",
            "current_phase": current_phase,
            "phase_entered_at": phase_entered_at,
            "cycles_completed": cycles_completed,
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("previous", "new", "expected_level", "expected_direction"),
    [
        ("settling", "warming", "INFO", "forward"),
        ("active", "settling", "WARNING", "regression"),
    ],
)
async def test_phase_change_returns_complete_observable_event(
    monkeypatch: pytest.MonkeyPatch,
    previous: str,
    new: str,
    expected_level: str,
    expected_direction: str,
) -> None:
    async def trust(_account_id: str) -> SimpleNamespace:
        return SimpleNamespace(score=73, band="normal")

    monkeypatch.setattr(_transitions, "account_trust_score", trust)
    monkeypatch.setattr(
        _transitions,
        "compute_intensity",
        lambda _age, *, trust_band: SimpleNamespace(phase=new, band=trust_band),
    )
    monkeypatch.setattr(_transitions, "_now_iso", lambda: "2026-07-17T12:30:00+00:00")
    record = _state_record(
        current_phase=previous,
        phase_entered_at="2026-07-10T00:00:00+00:00",
    )

    phase, entered_at, event = await _transitions._resolve_phase_after_cycle("acc-1", 240.0, record)

    assert phase == new
    assert entered_at == "2026-07-17T12:30:00+00:00"
    assert event is not None
    assert event.level == expected_level
    assert event.extra == {
        "from_phase": previous,
        "to_phase": new,
        "direction": expected_direction,
        "trust_score": 73,
        "cycle_index": 5,
    }


@pytest.mark.asyncio
async def test_unchanged_phase_preserves_original_entry_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def trust(_account_id: str) -> SimpleNamespace:
        return SimpleNamespace(score=80, band="normal")

    monkeypatch.setattr(_transitions, "account_trust_score", trust)
    monkeypatch.setattr(
        _transitions,
        "compute_intensity",
        lambda _age, *, trust_band: SimpleNamespace(phase="warming", band=trust_band),
    )
    monkeypatch.setattr(_transitions, "_now_iso", lambda: "unexpected-new-timestamp")
    record = _state_record(
        current_phase="warming",
        phase_entered_at="2026-07-10T00:00:00+00:00",
    )

    phase, entered_at, event = await _transitions._resolve_phase_after_cycle("acc-1", 240.0, record)

    assert (phase, entered_at, event) == (
        "warming",
        "2026-07-10T00:00:00+00:00",
        None,
    )
