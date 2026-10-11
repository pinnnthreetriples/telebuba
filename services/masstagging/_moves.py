"""Target moves shared by the workers and the board actions.

Every move is a compare-and-set on the states a target may be leaving — a worker's own
targets are ``working`` — so a manual action from the board is never overwritten by a
worker that read the row a moment earlier. Every move leaves a history entry and one
``masstagging_*`` log line naming the target and the reason, never anything secret.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from core.logging import log_event
from core.repositories import masstagging as repository
from schemas.masstagging_records import JournalRecord
from services.masstagging import _seams
from services.masstagging.pace import day_of

if TYPE_CHECKING:
    from schemas.masstagging_board import MasstaggingTargetReason, MasstaggingTargetState
    from schemas.masstagging_records import TargetRecord
    from schemas.telegram_actions_masstagging import MasstagUser
    from services.masstagging._context import RunContext

# Still in work; the board's "in work" tab.
ACTIVE_STATES: Final = ("queued", "primed", "working", "flood")
# What a worker may pick up.
PICKABLE: Final = ("queued", "primed", "flood")
_WORKING: Final = ("working",)


def _extra(ctx: RunContext, target: TargetRecord, **fields: object) -> dict[str, object]:
    return {"campaign_id": ctx.campaign_id, "target": target.raw, **fields}


async def skip(
    ctx: RunContext,
    target: TargetRecord,
    reason: MasstaggingTargetReason,
    account_id: str | None,
    *,
    expect: tuple[str, ...] = _WORKING,
) -> bool:
    now = _seams.now()
    moved = await repository.update_target(
        ctx.campaign_id,
        target.target_key,
        at=now,
        expect_states=expect,
        state="skipped",
        reason=reason,
        next_action_unix=None,
    )
    if moved:
        await repository.add_events(
            ctx.campaign_id,
            [
                JournalRecord(
                    target_key=target.target_key,
                    event="skipped",
                    at_unix=now,
                    account_id=account_id,
                    detail=reason,
                )
            ],
        )
        await log_event(
            "INFO",
            "masstagging_target_skipped",
            account_id=account_id,
            extra=_extra(ctx, target, reason=reason),
        )
    return moved


async def fail(
    ctx: RunContext, target: TargetRecord, reason: MasstaggingTargetReason, account_id: str
) -> None:
    now = _seams.now()
    moved = await repository.update_target(
        ctx.campaign_id,
        target.target_key,
        at=now,
        expect_states=_WORKING,
        state="failed",
        reason=reason,
        next_action_unix=None,
    )
    if moved:
        await repository.add_events(
            ctx.campaign_id,
            [
                JournalRecord(
                    target_key=target.target_key,
                    event="failed",
                    at_unix=now,
                    account_id=account_id,
                    detail=reason,
                )
            ],
        )
        await log_event(
            "WARNING",
            "masstagging_target_failed",
            account_id=account_id,
            extra=_extra(ctx, target, reason=reason),
        )


async def resolved(
    ctx: RunContext, target: TargetRecord, user: MasstagUser, account_id: str
) -> TargetRecord:
    """Remember who the target is, and which session can address them."""
    await repository.update_target(
        ctx.campaign_id,
        target.target_key,
        at=_seams.now(),
        resolved_user_id=user.user_id,
        resolved_by=account_id,
        username=user.username,
        first_name=user.first_name,
    )
    return target.model_copy(
        update={
            "resolved_user_id": user.user_id,
            "resolved_by": account_id,
            "username": user.username,
            "first_name": user.first_name,
        }
    )


async def primed(ctx: RunContext, target: TargetRecord, account_id: str) -> None:
    now = _seams.now()
    await repository.update_target(
        ctx.campaign_id, target.target_key, at=now, expect_states=_WORKING, account_id=account_id
    )
    await repository.add_events(
        ctx.campaign_id,
        [
            JournalRecord(
                target_key=target.target_key,
                event="dialog_created",
                at_unix=now,
                account_id=account_id,
            )
        ],
    )
    await log_event(
        "INFO", "masstagging_dialog_created", account_id=account_id, extra=_extra(ctx, target)
    )


async def tagged(
    ctx: RunContext,
    targets: list[TargetRecord],
    account_id: str,
    *,
    story_id: int | None = None,
) -> None:
    """These people are tagged (in one story, or one primed DM)."""
    now = _seams.now()
    keys = [target.target_key for target in targets]
    moved = set(
        await repository.update_targets(
            ctx.campaign_id,
            keys,
            at=now,
            expect_states=_WORKING,
            state="tagged",
            reason=None,
            account_id=account_id,
            story_id=story_id,
            next_action_unix=None,
        )
    )
    done = [target for target in targets if target.target_key in moved]
    await repository.register_tagged(
        [t.resolved_user_id for t in done if t.resolved_user_id is not None],
        campaign_id=ctx.campaign_id,
        at=now,
    )
    count = str(len(targets))
    await repository.add_events(
        ctx.campaign_id,
        [
            JournalRecord(
                target_key=t.target_key,
                event="tagged",
                at_unix=now,
                account_id=account_id,
                detail=count if ctx.stories else None,
            )
            for t in done
        ],
    )
    if done:
        await repository.bump_day(account_id, day_of(now), tags=len(done))
    for target in done:
        await log_event(
            "INFO",
            "masstagging_target_tagged",
            account_id=account_id,
            extra=_extra(ctx, target, mode=ctx.settings.mode),
        )


async def put_back(
    ctx: RunContext,
    targets: list[TargetRecord],
    state: MasstaggingTargetState,
    *,
    account_id: str | None,
    next_action_unix: int | None = None,
) -> None:
    """Targets a step did not finish go back to wait — for this account or any."""
    await repository.update_targets(
        ctx.campaign_id,
        [target.target_key for target in targets],
        at=_seams.now(),
        expect_states=_WORKING,
        state=state,
        reason=None,
        account_id=account_id,
        next_action_unix=next_action_unix,
    )
