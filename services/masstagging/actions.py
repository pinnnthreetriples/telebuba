"""Manual actions from the board: tag now, skip, put back in the queue.

Each is a compare-and-set on the states the target must be in, so a click that lands
after the engine moved the target on answers 409 ``masstagging_target_state_changed``
instead of undoing what the engine did. «Сейчас» moves the person to the head of the
queue and makes them due at once; a running account takes them on its next step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.logging import log_event
from core.repositories import masstagging as repository
from schemas.masstagging_records import JournalRecord
from services.masstagging import _moves, _seams
from services.masstagging._context import RunContext
from services.masstagging._errors import (
    TARGET_NOT_FOUND,
    TARGET_STATE_CHANGED,
    MasstaggingConflictError,
)
from services.masstagging.campaigns import settings_of

if TYPE_CHECKING:
    from schemas.masstagging import MasstaggingTargetAction
    from schemas.masstagging_records import TargetRecord

_DONE = ("failed", "skipped")


async def act_on_target(campaign_id: str, request: MasstaggingTargetAction) -> bool:
    """Apply one board action. ``False`` means no such campaign."""
    record = await repository.fetch_campaign(campaign_id)
    if record is None:
        return False
    target = await repository.fetch_target(campaign_id, request.target_key)
    if target is None:
        raise MasstaggingConflictError(TARGET_NOT_FOUND)
    ctx = RunContext(
        campaign_id=campaign_id,
        run_id=record.run_id or "",
        settings=settings_of(record),
        session_unix=record.session_unix or 0,
    )
    if request.action == "skip":
        moved = await _moves.skip(ctx, target, "manual", None, expect=_moves.PICKABLE)
    elif request.action == "now":
        moved = await _now(ctx, target)
    else:
        moved = await _requeue(ctx, target)
    if not moved:
        raise MasstaggingConflictError(TARGET_STATE_CHANGED)
    return True


async def _now(ctx: RunContext, target: TargetRecord) -> bool:
    targets = await repository.list_targets(ctx.campaign_id)
    head = min((t.position for t in targets), default=0) - 1
    now = _seams.now()
    moved = await repository.update_target(
        ctx.campaign_id,
        target.target_key,
        at=now,
        expect_states=_moves.PICKABLE,
        position=head,
        next_action_unix=None,
    )
    if moved:
        await _note(ctx, target, now, requeued=False)
        await log_event("INFO", "masstagging_target_now", extra=_extra(ctx, target))
    return moved


async def _requeue(ctx: RunContext, target: TargetRecord) -> bool:
    now = _seams.now()
    moved = await repository.update_target(
        ctx.campaign_id,
        target.target_key,
        at=now,
        expect_states=_DONE,
        state="queued",
        reason=None,
        account_id=None,
        next_action_unix=None,
    )
    if moved:
        await _note(ctx, target, now, requeued=True)
        await log_event("INFO", "masstagging_target_requeued", extra=_extra(ctx, target))
    return moved


async def _note(ctx: RunContext, target: TargetRecord, now: int, *, requeued: bool) -> None:
    await repository.add_events(
        ctx.campaign_id,
        [
            JournalRecord(
                target_key=target.target_key, event="requeued" if requeued else "now", at_unix=now
            )
        ],
    )


def _extra(ctx: RunContext, target: TargetRecord) -> dict[str, object]:
    return {"campaign_id": ctx.campaign_id, "target": target.raw}
