"""«Оживление чата» — replaying the dialogue in the operator's OWN chat, for ever.

A different mode rather than a setting, because three of the engine's assumptions
stop holding at once.

* **Nobody joins.** The accounts are already members of a chat the operator runs,
  so there is no invite to accept, no join to pace and no daily join budget to
  spend. ``engine._enter`` skips straight to the per-account resolve.
* **There is no denominator.** The run loops until it is stopped, so "sent out of
  total" has no total; the launch card shows the count itself.
* **Every cycle is a different conversation.** The journal is unique on
  ``(run_id, target, step_id)`` — which is exactly right for a campaign, whose
  whole safety property is never playing a step into a chat twice — so a second
  cycle under the same id would insert nothing and post nothing. Each cycle
  therefore writes under ``f"{run_id}#{n}"``, and ``_tables.run_scope`` is what
  keeps the run-wide questions (what did it deliver, what did it leave mid-flight)
  seeing those rows.

The campaign's ``run_id`` itself is NOT re-minted per cycle, and that is
deliberate: it is the identity Stop settles against, boot reconciliation resumes,
and ``_state`` fences generations by. Rewriting it mid-run would leave the
settlement path comparing a cycle id against a run id and refusing to settle at
all. The cycle number is a suffix on the journal key and nothing else.

Nothing here mentions a product: the separate generation prompt
(``_prompt.build_prompt(revive=True)``) forbids it outright, because a chat the
operator owns is being made to look alive rather than sold to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from services import pacing
from services.neuroshilling import _seams

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from schemas.neuroshilling_scenario import NeuroshillingStep
    from services.neuroshilling._context import RunContext

    _Enter = Callable[[RunContext, str], Awaitable[dict[str, int]]]
    _Act = Callable[
        [RunContext, str, Sequence[NeuroshillingStep], dict[str, int]],
        Awaitable[None],
    ]


def cycle_pause() -> float:
    """The gap between two cycles, jittered like every other pause in the engine.

    Separate from ``listen_minutes`` and added to it rather than replacing it: the
    listening window runs inside a cycle, so a campaign with both on waits for the
    window and then for this. Two dials, because "how long we keep reading" and
    "how often the conversation restarts" are different questions.
    """
    limits = settings.neuroshilling
    return pacing.human_delay(
        limits.revive_cycle_min_seconds,
        limits.revive_cycle_max_seconds,
        rng=_seams.rng,
        mu=limits.delay_lognorm_mu,
        sigma=limits.delay_lognorm_sigma,
    )


def _cycle_context(context: RunContext, cycle: int) -> RunContext:
    """The same run, writing its journal under this cycle's own key."""
    return context._replace(run_id=f"{context.run_id}#{cycle}")


async def _resolve_all(
    context: RunContext,
    targets: Sequence[str],
    enter: _Enter,
) -> list[tuple[str, dict[str, int]]]:
    """Resolve every target once, before the first cycle.

    Once and not per cycle: a chat id comes out of an account's own session entity
    cache and does not change, so re-resolving every quarter of an hour would be a
    request per account per cycle for an answer we already have.
    """
    entered: list[tuple[str, dict[str, int]]] = []
    for target in targets:
        chats = await enter(context, target)
        if chats:
            entered.append((target, chats))
        else:
            await log_event(
                "WARNING",
                "neuroshilling_target_failed",
                extra={"campaign_id": context.campaign.campaign_id, "target": target},
            )
    return entered


async def run(
    context: RunContext,
    targets: Sequence[str],
    enter: _Enter,
    act: _Act,
) -> None:
    """Play the dialogue round and round until the run is stopped.

    The two engine halves arrive as arguments rather than by import: this module is
    reached FROM ``engine``, and importing back into it would be a cycle. What is
    passed is what a revive cycle actually needs — a way into a chat and a way to
    act in one — and nothing about how either is done belongs here.

    The loop ends by cancellation (Stop, or shutdown) or when every account that
    could speak has been halted. It does not end by itself, which is the mode.
    """
    entered = await _resolve_all(context, targets, enter)
    if not entered:
        return
    cycle = 0
    while any(
        account_id not in context.halted for _target, chats in entered for account_id in chats
    ):
        cycle += 1
        current = _cycle_context(context, cycle)
        await log_event(
            "INFO",
            "neuroshilling_revive_cycle",
            extra={"campaign_id": context.campaign.campaign_id, "cycle": cycle},
        )
        for target, chats in entered:
            await act(current, target, current.steps, chats)
        await _seams.sleep(cycle_pause())
