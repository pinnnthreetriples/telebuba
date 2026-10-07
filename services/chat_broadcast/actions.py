"""Manual actions from the board: write now, hand to another account, skip the chat.

Each is a compare-and-set on the state the chat must be in, so a click that lands after
the engine moved the chat on answers 409 ``target_state_changed`` instead of undoing
what the engine did.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.repositories import chat_broadcast as repository
from services.chat_broadcast import _moves, _seams
from services.chat_broadcast._context import RunContext
from services.chat_broadcast._errors import (
    ACCOUNT_NOT_IN_CAMPAIGN,
    TARGET_NOT_FOUND,
    TARGET_STATE_CHANGED,
    ChatBroadcastConflictError,
    ChatBroadcastInvalidError,
)
from services.chat_broadcast.campaigns import settings_of

if TYPE_CHECKING:
    from schemas.chat_broadcast import ChatBroadcastTargetAction
    from schemas.chat_broadcast_records import TargetRecord

# A chat being joined or passing a captcha has a call in flight; moving it now would
# race that call.
_HANDABLE = ("queued", "pending_approval", "waiting", "writing", "reconnecting", "waiting_account")


async def act_on_target(campaign_id: str, request: ChatBroadcastTargetAction) -> bool:
    """Apply one board action. ``False`` means no such campaign."""
    record = await repository.fetch_campaign(campaign_id)
    if record is None:
        return False
    target = await repository.fetch_target(campaign_id, request.chat_key)
    if target is None:
        raise ChatBroadcastConflictError(TARGET_NOT_FOUND)
    ctx = RunContext(
        campaign_id=campaign_id,
        run_id=record.run_id or "",
        settings=settings_of(record),
        chain=[],
    )
    if request.action == "now":
        moved = target.state == "waiting" and await repository.update_target(
            campaign_id,
            target.chat_key,
            expect_states=("waiting",),
            next_action_unix=_seams.now(),
        )
    elif request.action == "skip":
        moved = await _moves.skip(ctx, target, "manual", None)
    else:
        moved = await _hand(ctx, campaign_id, target, request.account_id)
    if not moved:
        raise ChatBroadcastConflictError(TARGET_STATE_CHANGED)
    return True


async def _hand(
    ctx: RunContext, campaign_id: str, target: TargetRecord, account_id: str | None
) -> bool:
    roster = {item.account_id: item for item in await repository.list_roster(campaign_id)}
    entry = roster.get(account_id or "")
    if entry is None or entry.state != "active":
        raise ChatBroadcastInvalidError(ACCOUNT_NOT_IN_CAMPAIGN)
    if target.state not in _HANDABLE or target.assigned_account_id == entry.account_id:
        return False
    return await _moves.assign(ctx, target, entry.account_id)
