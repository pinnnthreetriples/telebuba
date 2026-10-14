"""The warming pace in force (Прогрев 2.0, item 5.2): env → preset → custom.

Every reader of the per-phase daily caps, the in-visit pauses and the persona DM
chances goes through ``effective_tuning`` — the phase ceiling, the visit cadence,
the human pause and the DM roll — so the card's choice reaches the engine without a
restart. «Обычно» returns the configured values themselves, not copies, so it is the
engine exactly as before, byte for byte. Phase boundaries and the per-session action
estimate stay in ``.env``: a preset changes how much an account does, not when it
grows up.

The stored choice rides the settings row and its in-process cache (one uvicorn
worker), which the write drops; the values are composed per call from the live
config, so an ``.env`` change or a test's patch is honoured at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.config import settings
from core.repositories.warming_tuning import cached_warming_tuning

if TYPE_CHECKING:
    from collections.abc import Mapping

    from schemas._warming_tuning import StoredTuning, TuningPreset


@dataclass(frozen=True, slots=True)
class EffectiveTuning:
    """The pace one decision runs under."""

    preset: TuningPreset
    phase_daily_cap: Mapping[str, int]
    pause_factor: float
    dm_factor: float
    persona_dm_probability: Mapping[str, float]


def _dm_chances(factor: float) -> Mapping[str, float]:
    configured = settings.warming.persona_dm_probability
    if factor == 1.0:
        return configured
    return {persona: min(1.0, chance * factor) for persona, chance in configured.items()}


def compose_tuning(stored: StoredTuning) -> EffectiveTuning:
    """Resolve a stored choice against the live config — pure."""
    warm = settings.warming
    caps: Mapping[str, int]
    if stored.preset == "careful":
        caps, pause, dm = (
            warm.tuning_careful_daily_cap,
            warm.tuning_careful_pause_factor,
            warm.tuning_careful_dm_factor,
        )
    elif stored.preset == "fast":
        caps, pause, dm = (
            warm.tuning_fast_daily_cap,
            warm.tuning_fast_pause_factor,
            warm.tuning_fast_dm_factor,
        )
    elif stored.preset == "custom" and stored.custom is not None:
        custom = stored.custom
        caps = {str(phase): cap for phase, cap in custom.daily_caps.items()}
        pause, dm = custom.pause_factor, custom.dm_factor
    else:
        # «Обычно», and a «Свой» whose limits went missing: the engine as configured.
        return EffectiveTuning(
            preset="normal",
            phase_daily_cap=warm.phase_daily_cap,
            pause_factor=1.0,
            dm_factor=1.0,
            persona_dm_probability=warm.persona_dm_probability,
        )
    return EffectiveTuning(
        preset=stored.preset,
        phase_daily_cap=caps,
        pause_factor=pause,
        dm_factor=dm,
        persona_dm_probability=_dm_chances(dm),
    )


def effective_tuning() -> EffectiveTuning:
    """The pace in force now, from the cached settings row."""
    return compose_tuning(cached_warming_tuning())
