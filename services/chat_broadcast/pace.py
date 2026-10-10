"""The board's gear: the pauses, saved at any time — a running campaign included.

The rest of the settings stay locked while a run is attached (the run reads them once),
but the pauses are read live: the save lands in the row, the running engine sees it
through ``_state`` from its next pause on, and a rest already under way is drawn again
from the moment it began, so shortening it can end it at once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.logging import log_event
from core.repositories import chat_broadcast as repository
from services.chat_broadcast import _state
from services.chat_broadcast._coordinator import rest_end
from services.chat_broadcast._errors import CAMPAIGN_CHANGED, ChatBroadcastConflictError
from services.chat_broadcast.campaigns import load_settings, settings_of

if TYPE_CHECKING:
    from schemas.chat_broadcast import ChatBroadcastPace, ChatBroadcastSettingsRead
    from schemas.chat_broadcast_records import CampaignRecord


async def save_pace(campaign_id: str, pace: ChatBroadcastPace) -> ChatBroadcastSettingsRead | None:
    """Store the pauses; ``None`` means no such campaign."""
    record = await repository.fetch_campaign(campaign_id)
    if record is None:
        return None
    current = settings_of(record)
    updated = current.model_copy(
        update={
            "between_messages": pace.between_messages,
            "between_chats": pace.between_chats,
            "rest_minutes": pace.rest_minutes,
        }
    )
    if not await repository.save_pace(
        campaign_id,
        settings_json=updated.model_dump_json(),
        expected_updated_at=record.updated_at,
    ):
        raise ChatBroadcastConflictError(CAMPAIGN_CHANGED)
    _state.set_pace(campaign_id, pace)
    until = None
    if pace.rest_minutes != current.rest_minutes:
        until = await _redraw_rest(record, pace)
    extra: dict[str, object] = {"campaign_id": campaign_id}
    if until is not None:
        extra["until"] = until
    await log_event("INFO", "chat_broadcast_pace_changed", extra=extra)
    return await load_settings(campaign_id)


async def _redraw_rest(record: CampaignRecord, pace: ChatBroadcastPace) -> int | None:
    """Move the end of the rest under way; ``None`` when there is none to move.

    A rest whose start this process never saw (it began before a restart) keeps its end:
    the new range applies from the next rest.
    """
    started = _state.rest_started(record.campaign_id)
    if record.rest_until_unix is None or started is None:
        return None
    until = rest_end(started, pace.rest_minutes)
    moved = await repository.move_rest(record.campaign_id, round_number=record.round, until=until)
    return until if moved else None
