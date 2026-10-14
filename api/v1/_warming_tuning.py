"""«Темп прогрева»: the warming pace presets (Прогрев 2.0, item 5.2).

Split-sibling of ``warming.py``, mounted onto its router like the pool routes.
"""

from __future__ import annotations

from fastapi import APIRouter

from schemas._warming_tuning import WarmingTuning, WarmingTuningUpdate
from services.warming import tuning

tuning_router = APIRouter()


@tuning_router.get("/tuning", response_model=WarmingTuning, operation_id="getWarmingTuning")
async def get_warming_tuning() -> WarmingTuning:
    """The pace in force: the preset, its per-phase limits and the visits they make."""
    return await tuning.load_tuning()


@tuning_router.put(
    "/tuning",
    response_model=WarmingTuning,
    operation_id="updateWarmingTuning",
)
async def update_warming_tuning(body: WarmingTuningUpdate) -> WarmingTuning:
    """Pick «Осторожно», «Обычно», «Быстро» or «Свой» with its own limits."""
    return await tuning.save_tuning(body)
