"""The *extras* cycle step — the tuning card's side actions, drawn per persona.

Runs last in ``run_one_cycle`` (after stories and the DM) so the budget-booking steps
never starve behind it. Registry-driven: ``EXTRAS`` lists one spec per toggle the
backend implements; ``_pick_extras`` keeps the toggled-on ones and samples
without replacement (so "at most once per cycle per key" is free), uniformly — a
weight would be one more tunable and the heavy actions are already gated by their
toggles. Timing and randomness reach the outside only via ``_seams``; no blanket
``except``: a revoked lease or a cancellation must unwind the cycle as it does today.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from services.warming import _extras_reads, _seams
from services.warming._extras_ctx import _ExtraSpec
from services.warming._steps import _human_pause
from services.warming.pacing import _FAILURE_STATUSES, _WAIT_STATUSES, _classify_flood

if TYPE_CHECKING:
    from collections.abc import Mapping
    from random import Random

    from schemas.telegram_actions import ActionResult
    from services.warming._extras_ctx import _ExtraContext
    from services.warming._steps import _ChannelTally

# PR1: the account-read family. The remaining ``ExtraToggles`` keys land per PR and
# the contract test is ``⊆`` until then.
EXTRAS: tuple[_ExtraSpec, ...] = (
    _ExtraSpec("dialogs", "read", _extras_reads.dialogs),
    _ExtraSpec("contacts", "read", _extras_reads.contacts),
    _ExtraSpec("notifications", "read", _extras_reads.notifications),
    _ExtraSpec("check_settings", "read", _extras_reads.check_settings),
    _ExtraSpec("view_profiles", "read", _extras_reads.view_profiles),
)


def _pick_extras(
    ctx: _ExtraContext, toggles: Mapping[str, object], rng: Random
) -> list[_ExtraSpec]:
    eligible = [s for s in EXTRAS if toggles.get(s.key, False)]
    lo, hi = settings.warming.persona_extras[ctx.persona]
    return rng.sample(eligible, min(rng.randint(lo, hi), len(eligible)))


def _fold_extra(tally: _ChannelTally, result: ActionResult) -> bool:
    """Fold one extra's outcome into the tally. True = stop this cycle's extras."""
    if result.status == "ok":
        tally.extras += 1
        return False
    if result.status == "peer_flood":
        tally.peer_flooded = True
        tally.last_failed_action = result.action_type
        return True
    if result.status in _WAIT_STATUSES:
        tally.flooded, tally.flood_seconds, tally.flood_until = _classify_flood(result)
        tally.last_failed_action = result.action_type
        return True
    if result.status in _FAILURE_STATUSES:
        tally.failures += 1
        tally.last_failed_action = result.action_type
    return False


async def run_extras_step(ctx: _ExtraContext) -> bool:
    """Run this cycle's drawn extras. True iff at least one landed (advances the rail)."""
    if ctx.tally.flooded or ctx.tally.peer_flooded:
        return False
    warm = settings.warming
    before = ctx.tally.extras
    for spec in _pick_extras(ctx, ctx.secret.extra_toggles, _seams.rng):
        # Reads are free and always go; a write past the daily budget is skipped, never
        # queued — the same rule the channel loop applies to a react.
        if spec.kind == "write" and not ctx.can_attempt():
            continue
        result = await spec.run(ctx)
        if result is None:
            continue
        if _fold_extra(ctx.tally, result):
            break
        await _human_pause(warm.action_delay_min_seconds, warm.action_delay_max_seconds)
    return ctx.tally.extras > before
