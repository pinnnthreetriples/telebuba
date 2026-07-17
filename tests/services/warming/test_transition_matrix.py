"""Observable state-transition matrices for loop outcomes and generations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo

import pytest

from core.config import settings
from schemas.warming import WarmingCycleResult, WarmingStateRecord
from services.warming import _seams, _transitions


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> _FrozenDateTime:
        return cls(2026, 7, 17, 12, tzinfo=tz)


_NOW = _FrozenDateTime.now(UTC)


def _record(state: str, run_id: str | None = "gen-a") -> WarmingStateRecord:
    return WarmingStateRecord.model_validate(
        {
            "account_id": "acc-1",
            "state": state,
            "updated_at": "2026-07-17T12:00:00+00:00",
            "run_id": run_id,
        }
    )


@pytest.mark.parametrize(
    ("record", "run_id", "expected"),
    [
        (None, None, True),
        (None, "gen-a", False),
        (_record("active"), None, True),
        (_record("sleeping"), "gen-a", True),
        (_record("quarantine"), "gen-b", False),
        (_record("idle"), None, False),
        (_record("error"), "gen-a", False),
    ],
    ids=[
        "legacy-empty",
        "generation-needs-row",
        "legacy-active",
        "matching-generation",
        "replaced-generation",
        "idle-terminal",
        "error-terminal",
    ],
)
def test_active_generation_matrix(
    record: WarmingStateRecord | None, run_id: str | None, expected: object
) -> None:
    assert _transitions._matches_active_run(record, run_id) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected_state"),
    [
        (WarmingCycleResult(account_id="a", status="ok", attempted_actions=3), "sleeping"),
        (
            WarmingCycleResult(
                account_id="a", status="failed", attempted_actions=2, channels_read=1
            ),
            "sleeping",
        ),
        (
            WarmingCycleResult(
                account_id="a",
                status="failed",
                attempted_actions=1,
                last_failed_action="set_online",
            ),
            "sleeping",
        ),
        (
            WarmingCycleResult(account_id="a", status="failed", attempted_actions=2, failures=2),
            "error",
        ),
        (WarmingCycleResult(account_id="a", status="peer_flood"), "quarantine"),
        (
            WarmingCycleResult(account_id="a", status="flood_wait", flood_wait_seconds=30),
            "flood_wait",
        ),
    ],
)
async def test_cycle_result_selects_safe_next_state(
    monkeypatch: pytest.MonkeyPatch,
    result: WarmingCycleResult,
    expected_state: str,
) -> None:
    monkeypatch.setattr(settings.warming, "active_hours_enabled", False)
    monkeypatch.setattr(settings.warming, "next_run_jitter_fraction", 0.0)
    monkeypatch.setattr(settings.warming, "flood_wait_margin_fraction", 0.0)
    monkeypatch.setattr(_transitions, "datetime", _FrozenDateTime)

    actions, next_run, state = await _transitions._calculate_next_run("acc-1", result, "normal", 40)

    assert actions == result.attempted_actions
    assert state == expected_state
    assert next_run > _NOW


@pytest.mark.asyncio
async def test_peer_flood_uses_quarantine_duration_without_active_hour_shift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.warming, "quarantine_hours", 7.0)
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(_transitions, "datetime", _FrozenDateTime)
    shifted: list[datetime] = []

    def shift(value: datetime, *_args: object, **_kwargs: object) -> datetime:
        shifted.append(value)
        return value

    monkeypatch.setattr(_transitions, "_shift_to_active_hours", shift)
    _, next_run, state = await _transitions._calculate_next_run(
        "acc-1", WarmingCycleResult(account_id="acc-1", status="peer_flood"), "normal", 40
    )

    assert state == "quarantine"
    assert next_run == _NOW + timedelta(hours=7)
    assert shifted == []


@pytest.mark.asyncio
async def test_flood_wait_zero_is_honoured_as_concrete_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.warming, "active_hours_enabled", False)
    monkeypatch.setattr(settings.warming, "flood_wait_margin_fraction", 0.0)
    monkeypatch.setattr(_seams.rng, "uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr(_transitions, "datetime", _FrozenDateTime)

    _, next_run, state = await _transitions._calculate_next_run(
        "acc-1",
        WarmingCycleResult(account_id="acc-1", status="flood_wait", flood_wait_seconds=0),
        "normal",
        40,
    )

    assert state == "flood_wait"
    assert next_run == _NOW
