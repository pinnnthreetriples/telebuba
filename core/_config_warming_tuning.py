"""The warming pace presets of the «Темп прогрева» card (Прогрев 2.0, item 5.2).

A mixin of :class:`core._config_warming.WarmingSettings`, like ``_config_warming_social``:
the fields are inherited, so their ``WARMING__*`` env names read like any other.
«Обычно» has no fields here on purpose — it is ``phase_daily_cap``, the in-visit pauses
and ``persona_dm_probability`` exactly as configured. The numbers below are where the
presets start (decision Q17); survival data will tune them.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

_PHASES = ("intro", "settling", "warming", "active", "warmed")
_Cap = Annotated[int, Field(ge=1, le=80)]


class WarmingTuningSettings(BaseModel):
    """Per-phase daily limits and pace of the «Осторожно» and «Быстро» presets."""

    tuning_careful_daily_cap: dict[str, _Cap] = Field(
        default_factory=lambda: {
            "intro": 10,
            "settling": 12,
            "warming": 15,
            "active": 30,
            "warmed": 60,
        },
    )
    tuning_fast_daily_cap: dict[str, _Cap] = Field(
        default_factory=lambda: {
            "intro": 15,
            "settling": 20,
            "warming": 30,
            "active": 50,
            "warmed": 80,
        },
    )
    # Multiplies every human pause inside a visit: longer when careful, shorter when fast.
    tuning_careful_pause_factor: float = Field(default=1.5, ge=0.5, le=3.0)
    tuning_fast_pause_factor: float = Field(default=0.7, ge=0.5, le=3.0)
    # Multiplies each persona's chance to start a DM: fewer when careful.
    tuning_careful_dm_factor: float = Field(default=0.5, ge=0.0, le=2.0)
    tuning_fast_dm_factor: float = Field(default=1.0, ge=0.0, le=2.0)
    # A limit below this is allowed but flagged on the card: too few actions to warm.
    tuning_low_cap_warning: int = Field(default=10, ge=1)

    @model_validator(mode="after")
    def _check_preset_caps(self) -> WarmingTuningSettings:
        # A missing phase is a KeyError mid-cycle and a dropping limit makes an older
        # account out-capped by a younger one; both fail at boot instead.
        for caps in (self.tuning_careful_daily_cap, self.tuning_fast_daily_cap):
            if set(caps) != set(_PHASES):
                msg = "a preset's daily caps must name exactly the five phases"
                raise ValueError(msg)
            ordered = [caps[phase] for phase in _PHASES]
            if any(later < earlier for earlier, later in pairwise(ordered)):
                msg = "a preset's daily cap must not drop from one phase to the next"
                raise ValueError(msg)
        return self
