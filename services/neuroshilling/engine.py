"""The sequential pass: every target in turn, every step of the dialogue in order.

One run is one coroutine walking ``targets_raw`` in the order the operator typed it.
Inside a target the participating accounts join and resolve the chat FIRST — a chat id
comes out of an account's own session entity cache, so each account has to fetch its
own — and only then are the steps played, each by whichever account of its role is
least busy.

**Parallel mode does not exist here**, and that is a decision rather than an omission:
it turns joins into a volley and quota re-counts into a race for no gain a sequential
pass does not already give. ``services.neuroshilling._runtime.start_campaign`` refuses
``run_mode='parallel'`` on the server for that reason. Three things are nonetheless
built parallel-safe from the first day, because retrofitting them into a hot path is
what costs: the reply anchor is keyed on ``(run_id, target, step_id)`` — the journal's
whole unique key, so two targets of one run never read each other's message ids — the
quota re-count and its insert share one lock, and the pacer is per account.

Resuming is the same code path: the run is handed the STORED ``run_id``, reads which
``(target, step)`` pairs already have a journal row, and skips them. Minting a fresh id
on resume would face an empty unique index and replay the whole dialogue into chats
that already have it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.repositories import neuroshilling as repository
from services import pacing
from services.neuroshilling import _seams, _steps, _telegram
from services.neuroshilling._context import RunContext
from services.neuroshilling.campaigns import parse_targets

if TYPE_CHECKING:
    from collections.abc import Sequence

    from schemas.neuroshilling_scenario import NeuroshillingStep

# Presence verdicts that are about the ACCOUNT rather than about this target, so the
# account stops being offered for the rest of the run.
_ACCOUNT_HALTED = frozenset({"flooded", "retired"})


async def run_campaign(campaign_id: str, run_id: str) -> None:
    """Play the whole campaign once. Raises only what the runtime is meant to catch."""
    loaded = await _load_context(campaign_id, run_id)
    if loaded is None:
        return
    context, targets, played = loaded
    speakers = _speakers(context)
    if not speakers:
        # Vacuously "everyone is halted" otherwise, which returned a run that had done
        # nothing and settled it ``done`` without a line anywhere. A roster edited
        # between two runs reaches here: the launch gate checks the roles, and a resumed
        # run never passes through it.
        await log_event(
            "WARNING",
            "neuroshilling_no_speakers",
            extra={"campaign_id": campaign_id},
        )
    entered = False
    for target in targets:
        pending = [step for step in context.steps if (target, step.step_id) not in played]
        if not pending:
            # Fully journalled on an earlier pass: no pause, no join, no resolve. This
            # is what keeps a resumed run from sleeping its way through finished work.
            continue
        if speakers and all(account_id in context.halted for account_id in speakers):
            # Nobody is left to say a line. Walking the rest of the target list would
            # pay the pause between each one to reach the same answer — a quarter of an
            # hour of nothing on a fifty-target campaign.
            return
        if entered:
            await _seams.sleep(_target_pause(context))
        entered = True
        await _play_target(context, target, pending)


async def _load_context(
    campaign_id: str,
    run_id: str,
) -> tuple[RunContext, list[str], set[tuple[str, str]]] | None:
    """Read everything the pass needs once. ``None`` means the campaign is gone."""
    campaign = await repository.fetch_campaign(campaign_id)
    if campaign is None:
        return None
    _roles, steps = await repository.load_scenario(campaign_id)
    accounts = await repository.list_campaign_accounts(campaign_id)
    by_role: dict[str, list[str]] = {}
    for account in accounts:
        # Reserve accounts are the substitution pool of a later stage, not speakers.
        if account.role_id is None or account.state != "active" or account.is_reserve:
            continue
        by_role.setdefault(account.role_id, []).append(account.account_id)
    context = RunContext(
        campaign=campaign,
        run_id=run_id,
        steps=list(steps),
        by_position={step.position: step for step in steps},
        by_role=by_role,
        halted=set(),
    )
    played = await repository.list_journalled_steps(run_id)
    return context, parse_targets(campaign.targets_raw), played


def _target_pause(context: RunContext) -> float:
    limits = settings.neuroshilling
    return pacing.human_delay(
        context.campaign.pause_min_seconds,
        context.campaign.pause_max_seconds,
        rng=_seams.rng,
        mu=limits.delay_lognorm_mu,
        sigma=limits.delay_lognorm_sigma,
    )


def _settle_pause() -> float:
    """The wait between entering a chat and saying the first word in it.

    Separate from the step delays, and floored by the settings model rather than by
    the operator: joining a group and broadcasting into it in the same second is the
    single most reportable thing this engine does.

    Applied on every entry into a target, including one that found its accounts
    already inside from an earlier pass. The alternative is a second presence read per
    (account, target) pair to learn which joins were fresh, and the cost of being
    wrong is one short sleep.
    """
    limits = settings.neuroshilling
    return pacing.human_delay(
        limits.post_join_settle_min_seconds,
        limits.post_join_settle_max_seconds,
        rng=_seams.rng,
        mu=limits.delay_lognorm_mu,
        sigma=limits.delay_lognorm_sigma,
    )


async def _play_target(
    context: RunContext,
    target: str,
    steps: Sequence[NeuroshillingStep],
) -> None:
    """Get the cast into one chat and act the dialogue out in it."""
    chats = await _enter(context, target)
    if not chats:
        await log_event(
            "WARNING",
            "neuroshilling_target_failed",
            extra={"campaign_id": context.campaign.campaign_id, "target": target},
        )
        return
    await _seams.sleep(_settle_pause())
    for step in steps:
        if not await _steps.play_step(context, target, step, chats):
            break


async def _enter(context: RunContext, target: str) -> dict[str, int]:
    """Join and resolve ``target`` for every account that has a line to say in it.

    Returns ``account_id -> chat_id``, and the id is per account on purpose: it comes
    out of that account's own session entity cache, which is a separate SQLite file,
    so the number one account resolved means nothing to another.

    Sequential rather than gathered: a volley of joins from one fleet at one chat is
    the shape Telegram freezes accounts over, and ``_telegram.join_target`` paces each
    of them through the join slot anyway.
    """
    campaign_id = context.campaign.campaign_id
    chats: dict[str, int] = {}
    for account_id in _speakers(context):
        if account_id in context.halted:
            continue
        state = await _telegram.join_target(campaign_id, account_id, target)
        if state in _ACCOUNT_HALTED:
            # An account verdict, not this target's: it plays nothing for the rest of
            # the run, and ``_telegram`` has already written that across its presence.
            context.halted.add(account_id)
            continue
        if state != "joined":
            continue
        resolved = await _telegram.resolve_target(campaign_id, account_id, target)
        if resolved is not None:
            chats[account_id] = resolved.chat_id
    return chats


def _speakers(context: RunContext) -> list[str]:
    """Every account with a role some step of this dialogue gives a line to.

    Walked in step order rather than over a set of role ids, so the accounts join in
    the order they will speak. A set iterates arbitrarily, and the joins are paced
    minutes apart — which would make the same campaign enter the same chat in a
    different order on every run, for no reason a reader could see.
    """
    seen: dict[str, None] = {}
    for step in context.steps:
        for account_id in context.by_role.get(step.role_id or "", ()):
            seen.setdefault(account_id, None)
    return list(seen)
