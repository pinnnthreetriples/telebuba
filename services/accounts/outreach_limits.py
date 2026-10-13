"""Outreach limits — the operator-editable pacing of the contact lookup and bulk messages.

Thin orchestration over ``core.repositories.outreach``. Each run reads the limits
when it starts, so a save takes effect on the next run, never mid-run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.repositories import outreach
from schemas.outreach_limits import OutreachUsageList
from schemas.outreach_presets import MAX_PRESETS, OutreachPresetList

if TYPE_CHECKING:
    from collections.abc import Sequence

    from schemas.outreach_limits import OutreachLimits, OutreachLimitsUpdate
    from schemas.outreach_presets import OutreachPreset, OutreachPresetWrite


async def load_outreach_limits() -> OutreachLimits:
    """The saved limits, or the defaults while none were saved."""
    return await outreach.load_outreach_limits()


async def save_outreach_limits(data: OutreachLimitsUpdate) -> OutreachLimits:
    """Replace every limit at once."""
    return await outreach.save_outreach_limits(data)


async def list_outreach_usage_today(account_ids: Sequence[str]) -> OutreachUsageList:
    """What each account already spent today (UTC), for the "left today" chips."""
    day = datetime.now(UTC).date().isoformat()
    usage = await outreach.list_outreach_usage(list(dict.fromkeys(account_ids)), day)
    return OutreachUsageList(day=day, items=list(usage.values()))


async def list_outreach_presets() -> OutreachPresetList:
    return OutreachPresetList(items=await outreach.list_outreach_presets())


_NAME_TAKEN = "preset_name_taken"


async def create_outreach_preset(data: OutreachPresetWrite) -> OutreachPreset:
    """Save a preset under a new name; refused past ``MAX_PRESETS`` or on a taken name."""
    if len(await outreach.list_outreach_presets()) >= MAX_PRESETS:
        msg = "too_many_presets"
        raise ValueError(msg)
    try:
        return await outreach.create_outreach_preset(data)
    except outreach.PresetNameTakenError as exc:
        raise ValueError(_NAME_TAKEN) from exc


async def update_outreach_preset(
    preset_id: str, data: OutreachPresetWrite
) -> OutreachPreset | None:
    try:
        return await outreach.update_outreach_preset(preset_id, data)
    except outreach.PresetNameTakenError as exc:
        raise ValueError(_NAME_TAKEN) from exc


async def delete_outreach_preset(preset_id: str) -> bool:
    return await outreach.delete_outreach_preset(preset_id)
