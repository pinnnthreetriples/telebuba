"""The warming pace card «Темп прогрева» (Прогрев 2.0, item 5.2) — data contract only.

A preset replaces editing ``.env``: «Обычно» is the configured engine as it is,
«Осторожно» and «Быстро» swap in their own per-phase daily limits and pace, «Свой» is
the operator's own limits. Self-contained (pydantic + the visits schema), so
``schemas._warming_settings`` can carry the stored choice without a cycle.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

# Pydantic resolves these at runtime, so they stay out of a TYPE_CHECKING block.
from schemas._warming_visits import CadencePreview, WarmingPhase  # noqa: TC001

TuningPreset = Literal["careful", "normal", "fast", "custom"]

# The phases in lifecycle order — a limit may never drop as an account ages.
TUNING_PHASES: tuple[WarmingPhase, ...] = ("intro", "settling", "warming", "active", "warmed")

# What one phase may be given a day: at least one action, at most the engine's ceiling
# (the 80 a months-old account gets).
DailyCap = Annotated[int, Field(ge=1, le=80)]


class WarmingTuningCustom(BaseModel):
    """«Свой»: the operator's own limit per phase and the pace around it."""

    daily_caps: dict[WarmingPhase, DailyCap]
    # Multiplies every human pause inside a visit (1 = as configured).
    pause_factor: float = Field(default=1.0, ge=0.5, le=3.0)
    # Multiplies each persona's chance to start a DM, capped at certainty.
    dm_factor: float = Field(default=1.0, ge=0.0, le=2.0)

    @model_validator(mode="after")
    def _every_phase_and_never_down(self) -> WarmingTuningCustom:
        if set(self.daily_caps) != set(TUNING_PHASES):
            msg = "daily_caps must name every phase"
            raise ValueError(msg)
        caps = [self.daily_caps[phase] for phase in TUNING_PHASES]
        if any(later < earlier for earlier, later in pairwise(caps)):
            msg = "a daily limit must not drop from one phase to the next"
            raise ValueError(msg)
        return self


class StoredTuning(BaseModel):
    """What the settings row holds: the chosen preset and the last «Свой» limits."""

    preset: TuningPreset = "normal"
    # Kept when another preset is picked, so «Свой» comes back as the operator left it.
    custom: WarmingTuningCustom | None = None


class WarmingTuningUpdate(BaseModel):
    """``PUT /warming/tuning``: pick a preset; «Свой» brings its limits along."""

    preset: TuningPreset
    custom: WarmingTuningCustom | None = None

    @model_validator(mode="after")
    def _custom_needs_limits(self) -> WarmingTuningUpdate:
        if self.preset == "custom" and self.custom is None:
            msg = "the custom preset needs its limits"
            raise ValueError(msg)
        return self


class TuningWarning(BaseModel):
    """Allowed but worth a second look — the UI words it; ``low_daily_cap`` for now."""

    code: Literal["low_daily_cap"]
    phase: WarmingPhase
    daily_cap: int


class WarmingTuning(BaseModel):
    """The pace in force: the preset, what it resolves to and the visits it makes."""

    preset: TuningPreset
    custom: WarmingTuningCustom | None = None
    # Phase → the daily limit in force, every phase present.
    daily_caps: dict[str, int]
    pause_factor: float
    dm_factor: float
    # Visits a day per persona and phase under these limits (the 4.3 cadence math).
    cadence: list[CadencePreview] = Field(default_factory=list)
    warnings: list[TuningWarning] = Field(default_factory=list)
