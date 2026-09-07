"""Write extras runners — the self-scoped half of the step (Saved Messages only).

Each runner dispatches through ``_write`` so the daily budget is booked before the
RPC leaves the process. The action models carry no destination: core hardcodes
``InputPeerSelf``, so nothing here can aim a note or a forward at another account.
Randomness and pacing go through ``_seams.rng`` / ``_human_pause`` like every other
warming module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from schemas.telegram_actions_warming import WarmForwardToSaved, WarmSaveDraft, WarmSelfNote
from services.warming import _seams
from services.warming._extras_ctx import _recent_posts, _write
from services.warming._steps import _human_pause

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult
    from services.warming._extras_ctx import _ExtraContext


def _note_text() -> str:
    return _seams.rng.choice(settings.warming.extras_note_texts)


async def saved(ctx: _ExtraContext) -> ActionResult:
    # ponytail: clearing old notes needs a table of our own message ids — deleting by
    # search could hit the operator's own Saved Messages. Notes accumulate for now.
    return await _write(ctx, WarmSelfNote(text=_note_text()))


async def scheduled(ctx: _ExtraContext) -> ActionResult:
    warm = settings.warming
    action = WarmSelfNote(
        text=_note_text(),
        schedule_in_hours=_seams.rng.uniform(*warm.extras_scheduled_delay_hours),
        cancel_reminder=_seams.rng.random() < warm.extras_reminder_cancel_probability,
    )
    return await _write(ctx, action)


async def drafts(ctx: _ExtraContext) -> ActionResult:
    """Type a draft; sometimes think better of it and clear it after a pause.

    Two budget-booking dispatches. The step checks the budget once per spec, so the
    clear re-checks it itself: a spent day skips the clear rather than overspending.
    """
    warm = settings.warming
    result = await _write(ctx, WarmSaveDraft(text=_note_text()))
    clear = _seams.rng.random() < warm.extras_draft_clear_probability
    if result.status != "ok" or not clear or not ctx.can_attempt():
        return result
    await _human_pause(warm.action_delay_min_seconds, warm.action_delay_max_seconds)
    return await _write(ctx, WarmSaveDraft(text=""))


async def forward(ctx: _ExtraContext) -> ActionResult:
    channel, ids = _recent_posts(ctx)
    action = WarmForwardToSaved(channel=channel, message_id=_seams.rng.choice(ids))
    return await _write(ctx, action)
