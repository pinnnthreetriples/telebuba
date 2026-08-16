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

**Two modes leave this module by different doors.** A ``campaign`` walks the target
list once and finishes; a ``revive`` plays one chat the operator owns round and round
until it is stopped, and ``_revive`` owns that shape. Both share ``_enter`` and
``_act``, which is why those two are separated at all.

Reading the chat is part of acting in it. When the campaign asks for it, ``_act`` polls
the target after the last line for ``listen_minutes`` — the pass is sequential, so that
window is paid per target and a long one on a long list is a long run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.repositories import neuroshilling as repository
from core.repositories.accounts import list_accounts_by_ids
from services import pacing
from services.neuroshilling import _listen, _revive, _seams, _steps, _telegram
from services.neuroshilling._context import RunContext
from services.neuroshilling.campaigns import parse_targets

if TYPE_CHECKING:
    from collections.abc import Sequence

    from schemas.neuroshilling_scenario import NeuroshillingStep


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
        # run never passes through it. Read before the mode split, because a revive
        # with nobody in its cast is the same silence on a loop.
        await log_event(
            "WARNING",
            "neuroshilling_no_speakers",
            extra={"campaign_id": campaign_id},
        )
    if context.campaign.mode == "revive":
        # A different shape entirely: no joins, no target list to get through, no
        # end. ``played`` is not consulted — a revive cycle is meant to say the
        # same lines again, and each one journals under a key of its own.
        await _revive.run(context, targets, _enter, _act)
        return
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
        # Reserve accounts are the substitution pool, not speakers, and a banned one is
        # finished for good — the row outlives the run-local halt set on purpose.
        if account.role_id is None or account.state != "active" or account.is_reserve:
            continue
        by_role.setdefault(account.role_id, []).append(account.account_id)
    # The whole roster and not just the speakers: a reserve promoted mid-run is one of
    # ours from the moment it says anything, and this set is read once and never again.
    rostered = await list_accounts_by_ids([account.account_id for account in accounts])
    context = RunContext(
        campaign=campaign,
        run_id=run_id,
        steps=list(steps),
        by_position={step.position: step for step in steps},
        by_role=by_role,
        halted=set(),
        banned={},
        banned_in={},
        our_user_ids=frozenset(
            account.user_id for account in rostered.accounts if account.user_id is not None
        ),
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
    await _act(context, target, steps, chats)


async def _act(
    context: RunContext,
    target: str,
    steps: Sequence[NeuroshillingStep],
    chats: dict[str, int],
) -> None:
    """Settle in, say the lines, then read what the room said back.

    Split from :func:`_play_target` because a revive cycle acts in a chat it entered
    once, several cycles ago, so "get in" and "act" cannot be one step there.

    The listening window comes last and only when the campaign asked for it; on a
    campaign that did not, ``_listen.listen`` returns without a single request and
    the target ends the moment its last line lands.
    """
    await _seams.sleep(_telegram.settle_pause())
    for step in steps:
        if not await _steps.play_step(context, target, step, chats):
            break
    await _listen.listen(context, target, chats)


async def _enter(context: RunContext, target: str) -> dict[str, int]:
    """Join and resolve ``target`` for every account that has a line to say in it.

    Returns ``account_id -> chat_id``, and the id is per account on purpose: it comes
    out of that account's own session entity cache, which is a separate SQLite file,
    so the number one account resolved means nothing to another.

    Sequential rather than gathered: a volley of joins from one fleet at one chat is
    the shape Telegram freezes accounts over, and ``_telegram.join_target`` paces each
    of them through the join slot anyway.

    A ``revive`` campaign skips the join half outright. Its one chat belongs to the
    operator and the accounts are already in it, so a join would spend the shared
    daily budget — the same counter neurocomment's onboarding draws on — on a request
    that can only answer ``already_participant``.
    """
    campaign_id = context.campaign.campaign_id
    joining = context.campaign.mode != "revive"
    chats: dict[str, int] = {}
    for account_id in _speakers(context):
        if account_id in context.halted:
            continue
        if joining:
            state = await _telegram.join_target(campaign_id, account_id, target)
            if state in _telegram.ACCOUNT_HALTED:
                # An account verdict, not this target's: it plays nothing for the rest
                # of the run, and ``_telegram`` has written that across its presence.
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
