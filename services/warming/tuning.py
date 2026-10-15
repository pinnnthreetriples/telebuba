"""«Темп прогрева»: read and switch the warming pace (Прогрев 2.0, item 5.2).

The card shows the preset in force, what it resolves to and the visits a day it makes
(the 4.3 cadence math, so the table is the engine's own numbers). Picking a preset is
one write; the engine sees it on its next decision (``_tuning.effective_tuning``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.repositories.warming import load_warming_settings
from core.repositories.warming_tuning import store_warming_tuning
from schemas._warming_tuning import (
    TUNING_PHASES,
    StoredTuning,
    TuningWarning,
    WarmingTuning,
    WarmingTuningUpdate,
)
from services.warming._cadence import cadence_preview
from services.warming._tuning import compose_tuning

if TYPE_CHECKING:
    from collections.abc import Mapping


def _low_caps(daily_caps: Mapping[str, int]) -> list[TuningWarning]:
    """Limits too low to warm on — allowed, but the card says so."""
    floor = settings.warming.tuning_low_cap_warning
    return [
        TuningWarning(code="low_daily_cap", phase=phase, daily_cap=daily_caps[phase])
        for phase in TUNING_PHASES
        if daily_caps[phase] < floor
    ]


def _view(stored: StoredTuning) -> WarmingTuning:
    effective = compose_tuning(stored)
    caps: dict[str, int] = {phase: effective.phase_daily_cap[phase] for phase in TUNING_PHASES}
    return WarmingTuning(
        preset=effective.preset,
        custom=stored.custom,
        daily_caps=caps,
        pause_factor=effective.pause_factor,
        dm_factor=effective.dm_factor,
        cadence=cadence_preview(caps),
        warnings=_low_caps(caps),
    )


async def load_tuning() -> WarmingTuning:
    """The pace in force, as the card shows it."""
    return _view((await load_warming_settings()).tuning)


async def save_tuning(update: WarmingTuningUpdate) -> WarmingTuning:
    """Switch the pace; «Свой» limits are kept when another preset is picked."""
    current = (await load_warming_settings()).tuning
    stored = StoredTuning(preset=update.preset, custom=update.custom or current.custom)
    await store_warming_tuning(stored)
    return _view(stored)
