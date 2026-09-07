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
from services.warming import _extras_chats, _extras_reads, _extras_writes, _seams
from services.warming._extras_ctx import _ExtraSpec
from services.warming._steps import _human_pause
from services.warming.pacing import _FAILURE_STATUSES, _WAIT_STATUSES, _classify_flood

if TYPE_CHECKING:
    from collections.abc import Mapping
    from random import Random

    from schemas.telegram_actions import ActionResult
    from services.warming._extras_ctx import _ExtraContext, _Need
    from services.warming._steps import _ChannelTally

# PR1 account reads + PR2 browse reads + PR3 Saved-Messages writes + PR4 chats and polls.
# The remaining ``ExtraToggles`` keys land in PR5 and the contract test is ``⊆`` until then.
_RECENT: frozenset[_Need] = frozenset({"recent_ids"})
_JOINED: frozenset[_Need] = frozenset({"joined"})
EXTRAS: tuple[_ExtraSpec, ...] = (
    _ExtraSpec("dialogs", "read", _extras_reads.dialogs),
    _ExtraSpec("contacts", "read", _extras_reads.contacts),
    _ExtraSpec("notifications", "read", _extras_reads.notifications),
    _ExtraSpec("check_settings", "read", _extras_reads.check_settings),
    _ExtraSpec("view_profiles", "read", _extras_reads.view_profiles),
    _ExtraSpec("search_messages", "read", _extras_reads.search_messages, _RECENT),
    _ExtraSpec("link_preview", "read", _extras_reads.link_preview, _RECENT),
    _ExtraSpec("gif", "read", _extras_reads.gif),
    _ExtraSpec("stickers", "read", _extras_reads.stickers),
    _ExtraSpec("inline_bots", "read", _extras_reads.inline_bots),
    _ExtraSpec("saved", "write", _extras_writes.saved),
    _ExtraSpec("scheduled", "write", _extras_writes.scheduled),
    _ExtraSpec("drafts", "write", _extras_writes.drafts),
    _ExtraSpec("forward", "write", _extras_writes.forward, _RECENT),
    _ExtraSpec("polls", "write", _extras_chats.polls, _RECENT),
    _ExtraSpec("leave", "write", _extras_chats.leave, _JOINED),
    _ExtraSpec("archive", "write", _extras_chats.archive, _JOINED),
    _ExtraSpec("mute", "write", _extras_chats.mute, _JOINED),
)


def _is_eligible(spec: _ExtraSpec, ctx: _ExtraContext) -> bool:
    # One fact per ``_Need``; a spec runs only when every fact it names holds.
    facts = {
        "recent_ids": any(ctx.recent_ids.values()),
        "joined": bool(_extras_chats.joined_now(ctx)),
    }
    return all(facts[need] for need in spec.needs)


def _pick_extras(
    ctx: _ExtraContext, toggles: Mapping[str, object], rng: Random
) -> list[_ExtraSpec]:
    eligible = [s for s in EXTRAS if toggles.get(s.key, False) and _is_eligible(s, ctx)]
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
