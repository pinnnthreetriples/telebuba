"""The ``warming_account_state`` row → :class:`WarmingStateRecord` mapper.

Its own module for the file-size budget of ``core.repositories.warming``, which imports
it back for every read of the table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from core.db import _optional_int, _optional_str, _required_int
from schemas.warming import WarmingStateRecord

if TYPE_CHECKING:
    from collections.abc import Mapping

    from schemas._warming_console import NcStartMode
    from schemas.warming import ActivityPersona, WarmingPhase, WarmingState


def _row_to_warming_state_record(mapping: Mapping[str, object]) -> WarmingStateRecord:
    phase_raw = _optional_str(mapping.get("current_phase"))
    return WarmingStateRecord(
        account_id=str(mapping["account_id"]),
        state=cast("WarmingState", mapping["state"]),
        cycles_completed=_required_int(mapping["cycles_completed"]),
        last_event=_optional_str(mapping.get("last_event")),
        last_cycle_at=_optional_str(mapping.get("last_cycle_at")),
        next_run_at=_optional_str(mapping.get("next_run_at")),
        updated_at=str(mapping["updated_at"]),
        last_error=_optional_str(mapping.get("last_error")),
        last_action=_optional_str(mapping.get("last_action")),
        last_channel=_optional_str(mapping.get("last_channel")),
        heartbeat_at=_optional_str(mapping.get("heartbeat_at")),
        started_at=_optional_str(mapping.get("started_at")),
        stopped_at=_optional_str(mapping.get("stopped_at")),
        flood_wait_seconds=_optional_int(mapping.get("flood_wait_seconds")),
        flood_wait_until=_optional_str(mapping.get("flood_wait_until")),
        proxy_snapshot=_optional_str(mapping.get("proxy_snapshot")),
        daily_actions=_optional_int(mapping.get("daily_actions")) or 0,
        daily_count_date=_optional_str(mapping.get("daily_count_date")),
        quarantine_count=_optional_int(mapping.get("quarantine_count")) or 0,
        run_id=_optional_str(mapping.get("run_id")),
        current_phase=cast("WarmingPhase | None", phase_raw),
        phase_entered_at=_optional_str(mapping.get("phase_entered_at")),
        promoted_to_nc=bool(mapping.get("promoted_to_nc") or 0),
        nc_handed_off=bool(mapping.get("nc_handed_off") or 0),
        target_days=_optional_int(mapping.get("target_days")),
        activity_persona=cast(
            "ActivityPersona",
            _optional_str(mapping.get("activity_persona")) or "normal",
        ),
        recovered_at=_optional_str(mapping.get("recovered_at")),
        spam_unknown_streak=_optional_int(mapping.get("spam_unknown_streak")) or 0,
        warmed_days=_optional_int(mapping.get("warmed_days")) or 0,
        last_warmed_date=_optional_str(mapping.get("last_warmed_date")),
        geo_ack=_optional_str(mapping.get("geo_ack")),
        cycle_base_actions=_optional_int(mapping.get("cycle_base_actions")),
        nc_start_mode=cast("NcStartMode | None", _optional_str(mapping.get("nc_start_mode"))),
        nc_handoff_at=_optional_str(mapping.get("nc_handoff_at")),
    )
