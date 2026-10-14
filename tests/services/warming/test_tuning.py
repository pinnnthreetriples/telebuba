"""The warming pace presets reach the engine (Прогрев 2.0, item 5.2)."""

from __future__ import annotations

import pytest

from core.config import settings
from core.repositories.warming_tuning import store_warming_tuning
from schemas._warming_tuning import StoredTuning, WarmingTuningCustom, WarmingTuningUpdate
from services.warming import _cadence, _steps, _tuning, tuning
from services.warming.pacing import compute_intensity, persona_dm_probability

_DAY = 24.0
# One age inside each phase at the shipped day bounds.
_AGES = {"intro": 0.5 * _DAY, "settling": 3 * _DAY, "warming": 10 * _DAY}
_AGES |= {"active": 20 * _DAY, "warmed": 60 * _DAY}


def _custom(**caps: int) -> WarmingTuningCustom:
    base = {"intro": 4, "settling": 6, "warming": 9, "active": 20, "warmed": 40}
    return WarmingTuningCustom(daily_caps={**base, **caps}, pause_factor=2.0, dm_factor=2.0)  # ty: ignore[invalid-argument-type]


def _caps_in_force() -> dict[str, int]:
    return {phase: compute_intensity(age).daily_cap for phase, age in _AGES.items()}


@pytest.mark.asyncio
async def test_normal_is_the_configured_engine_byte_for_byte() -> None:
    effective = _tuning.effective_tuning()

    # The very objects the engine read before the card existed, not copies.
    assert effective.preset == "normal"
    assert effective.phase_daily_cap is settings.warming.phase_daily_cap
    assert effective.persona_dm_probability is settings.warming.persona_dm_probability
    assert effective.pause_factor == 1.0
    assert _caps_in_force() == settings.warming.phase_daily_cap
    assert persona_dm_probability("normal") == settings.warming.persona_dm_probability["normal"]
    assert _cadence.cadence_preview() == _cadence.cadence_preview(settings.warming.phase_daily_cap)


@pytest.mark.asyncio
async def test_normal_follows_an_env_change_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    caps = {**settings.warming.phase_daily_cap, "warmed": 70}
    monkeypatch.setattr(settings.warming, "phase_daily_cap", caps)

    assert _caps_in_force()["warmed"] == 70


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("preset", "caps", "pause", "dm"),
    [
        (
            "careful",
            {"intro": 10, "settling": 12, "warming": 15, "active": 30, "warmed": 60},
            1.5,
            0.5,
        ),
        (
            "fast",
            {"intro": 15, "settling": 20, "warming": 30, "active": 50, "warmed": 80},
            0.7,
            1.0,
        ),
    ],
)
async def test_a_preset_sets_limits_pauses_and_dms(
    preset: str, caps: dict[str, int], pause: float, dm: float
) -> None:
    await tuning.save_tuning(WarmingTuningUpdate(preset=preset))  # ty: ignore[invalid-argument-type]

    effective = _tuning.effective_tuning()
    assert (effective.preset, effective.pause_factor, effective.dm_factor) == (preset, pause, dm)
    assert _caps_in_force() == caps
    expected_dm = settings.warming.persona_dm_probability["active"] * dm
    assert persona_dm_probability("active") == pytest.approx(expected_dm)


@pytest.mark.asyncio
async def test_custom_limits_drive_the_engine_and_a_dm_chance_stays_a_chance() -> None:
    await tuning.save_tuning(WarmingTuningUpdate(preset="custom", custom=_custom()))

    assert _caps_in_force() == {"intro": 4, "settling": 6, "warming": 9, "active": 20, "warmed": 40}
    # 0.55 × 2 would be 1.1: a probability never passes certainty.
    assert persona_dm_probability("active") == 1.0
    assert persona_dm_probability("calm") == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_the_cadence_table_follows_the_preset() -> None:
    before = _cadence.cadence_preview()
    view = await tuning.save_tuning(WarmingTuningUpdate(preset="custom", custom=_custom()))

    assert view.cadence == _cadence.cadence_preview()
    assert view.cadence != before
    intro_calm = next(r for r in view.cadence if (r.persona, r.phase) == ("calm", "intro"))
    # Four actions a day afford two short visits at most.
    assert intro_calm.visits_max <= 2


@pytest.mark.asyncio
async def test_switching_presets_keeps_the_custom_limits() -> None:
    await tuning.save_tuning(WarmingTuningUpdate(preset="custom", custom=_custom()))
    await tuning.save_tuning(WarmingTuningUpdate(preset="fast"))

    view = await tuning.load_tuning()

    assert view.preset == "fast"
    assert view.custom == _custom()
    assert view.warnings == []


@pytest.mark.asyncio
async def test_unreadable_stored_values_read_as_normal() -> None:
    # A «Свой» without its limits (an old or hand-edited row) must not stop the engine.
    await store_warming_tuning(StoredTuning(preset="custom", custom=None))

    view = await tuning.load_tuning()

    assert view.preset == "normal"
    assert view.daily_caps == settings.warming.phase_daily_cap


@pytest.mark.asyncio
async def test_the_pause_factor_stretches_every_human_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows: list[tuple[float, float]] = []

    def delay(low: float, high: float) -> float:
        windows.append((low, high))
        return 0.0

    monkeypatch.setattr(_steps, "_human_delay", delay)
    await _steps._human_pause(10.0, 30.0)
    await tuning.save_tuning(WarmingTuningUpdate(preset="careful"))
    await _steps._human_pause(10.0, 30.0)

    assert windows == [(10.0, 30.0), (15.0, 45.0)]


def test_a_custom_limit_never_drops_as_an_account_ages() -> None:
    with pytest.raises(ValueError, match="must not drop"):
        _custom(active=8)
