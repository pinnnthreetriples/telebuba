"""The board's gear: «Темп», saved at any time — a running campaign included.

The rest of the settings stay locked while a run is attached (the run reads them once),
but «Темп» is read live: the save lands in the row and the running engine sees it
through ``_state`` from its next step on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.logging import log_event
from core.repositories import masstagging as repository
from services.masstagging import _state
from services.masstagging._errors import CAMPAIGN_CHANGED, MasstaggingConflictError
from services.masstagging.campaigns import load_settings, settings_of

if TYPE_CHECKING:
    from schemas.masstagging import MasstaggingPace, MasstaggingSettingsRead


async def save_pace(campaign_id: str, pace: MasstaggingPace) -> MasstaggingSettingsRead | None:
    """Store «Темп»; ``None`` means no such campaign."""
    record = await repository.fetch_campaign(campaign_id)
    if record is None:
        return None
    fields = {name: getattr(pace, name) for name in type(pace).model_fields}
    updated = settings_of(record).model_copy(update=fields)
    if not await repository.save_pace(
        campaign_id,
        settings_json=updated.model_dump_json(),
        expected_updated_at=record.updated_at,
    ):
        raise MasstaggingConflictError(CAMPAIGN_CHANGED)
    _state.set_pace(campaign_id, pace)
    await log_event("INFO", "masstagging_pace_changed", extra={"campaign_id": campaign_id})
    return await load_settings(campaign_id)
