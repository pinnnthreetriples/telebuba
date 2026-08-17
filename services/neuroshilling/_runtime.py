"""Run lifecycle: start, stop, boot reconciliation, shutdown — and account ownership.

**Start is where neuroshilling becomes a WRITER of the ownership registry.** Warming
already claims and releases, and both warming's ``assert_not_neuroshilling`` and
neurocomment's ``busy_neuroshilling`` selection branch read it; until something claimed
on this side those two were guards over an always-empty map. :func:`start_campaign`
claims every account of the roster under the campaign's id, and the identity check in
``_account_owner.try_claim`` is what enforces the rule that an account may be ASSIGNED
to any number of campaigns but HELD by at most one running one.

**Stop is not a status flip.** ``status='stopping'`` is a row, and a coroutine asleep
inside a step delay does not read rows: it wakes up and posts. So Stop bumps the
campaign's run generation, which ``_seams`` checks before AND after every external
call — the "after" being for the call that was already in flight, whose outcome is
unknown — and then cancels the task and waits a bounded moment for it to unwind.

**A resumed run keeps the stored ``run_id``.** The repository idiom elsewhere is a
fresh ``uuid4().hex`` per restart; here that would face an empty unique index and
replay the entire dialogue into chats that already have it. Only :func:`start_campaign`
mints one, and only a terminal state clears it.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING
from uuid import uuid4

from core.config import settings
from core.logging import log_event
from core.repositories import neuroshilling as repository
from core.repositories.neurocomment import (
    get_listener_account_id,
    get_listener_running,
    list_active_campaign_account_names,
)
from services import _account_owner
from services.neuroshilling import _seams, _state, engine
from services.neuroshilling.campaigns import (
    NeuroshillingConflictError,
    parse_targets,
    run_status,
)

if TYPE_CHECKING:
    from schemas.neuroshilling import (
        NeuroshillingCampaign,
        NeuroshillingRefusalCode,
        NeuroshillingRunStatus,
        NeuroshillingStatus,
    )

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
logger = logging.getLogger(__name__)

_OWNER = "neuroshilling"
_LIVE_STATUSES = frozenset({"running", "stopping"})

_ACCOUNT_BUSY: NeuroshillingRefusalCode = "account_busy"
_ACCOUNT_IS_LISTENER: NeuroshillingRefusalCode = "account_is_listener"
_CAMPAIGN_RUNNING: NeuroshillingRefusalCode = "campaign_running"
_NOT_ENOUGH_ACCOUNTS: NeuroshillingRefusalCode = "not_enough_accounts"
_NO_TARGETS: NeuroshillingRefusalCode = "no_targets"
_ROLE_WITHOUT_ACCOUNT: NeuroshillingRefusalCode = "role_without_account"
_RUN_MODE_NOT_SUPPORTED: NeuroshillingRefusalCode = "run_mode_not_supported"
_SCENARIO_NOT_APPROVED: NeuroshillingRefusalCode = "scenario_not_approved"

# campaign_id -> the task playing it. In-memory and single-process, like every other
# runtime map in this project; a restart repairs from the campaign rows instead.
_TASKS: dict[str, asyncio.Task[None]] = {}


async def start_campaign(campaign_id: str) -> NeuroshillingRunStatus | None:
    """Launch a run. ``None`` means no such campaign; every refusal is a 409 code.

    The live-status test, the in-memory start claim and the run id are taken in ONE
    synchronous stretch, before the first roster read. The status column alone cannot
    carry "one running campaign": ``running`` is written three awaits later, so two
    requests both read an idle row, both pass, and ``_account_owner.try_claim`` cannot
    refuse the second either — it is idempotent for the same (owner, holder) pair. The
    result was two run tasks with ``_TASKS`` holding only the second, leaving the first
    unreachable to Stop and to shutdown.
    """
    campaign = await repository.fetch_campaign(campaign_id)
    if campaign is None:
        return None
    if campaign.status in _LIVE_STATUSES or not _state.try_claim_start(campaign_id):
        raise NeuroshillingConflictError(_CAMPAIGN_RUNNING)
    run_id = uuid4().hex
    try:
        _refuse_unlaunchable(campaign)
        account_ids = await _check_roster(campaign)
        await _claim_accounts(campaign_id, account_ids)
        try:
            generation = _state.begin_run(campaign_id, run_id)
            await repository.set_run_state(campaign_id, "running", run_id=run_id)
        except BaseException:
            _release_campaign(campaign_id)
            raise
        # Read BEFORE the spawn. One suspension point is all the run task needs to play
        # a short campaign to the end, and this call has three of them — so a status
        # read afterwards answered ``done`` for a run the caller was never told had
        # started, and the launch card showed a campaign that never appeared to run.
        started = await run_status(campaign_id)
        # A cancellation delivered AT the write above, after the row landed, does leave
        # a campaign marked running with nothing playing it. That state is recoverable
        # rather than lost: it is the same row a killed process leaves, and boot
        # reconciliation resumes it under the same run id.
        _spawn(campaign_id, run_id, generation)
    finally:
        _state.finish_start(campaign_id)
    await log_event("INFO", "neuroshilling_run_started", extra={"campaign_id": campaign_id})
    return started


def _refuse_unlaunchable(campaign: NeuroshillingCampaign) -> None:
    """The refusals that can be read off the campaign row alone."""
    if campaign.run_mode == "parallel":
        # Refused on the SERVER and not merely hidden: the generated client types the
        # field, so a direct call would otherwise start a mode with no engine behind it.
        raise NeuroshillingConflictError(_RUN_MODE_NOT_SUPPORTED)
    if campaign.scenario_status != "approved":
        raise NeuroshillingConflictError(_SCENARIO_NOT_APPROVED)
    if not parse_targets(campaign.targets_raw):
        raise NeuroshillingConflictError(_NO_TARGETS)


async def _check_roster(campaign: NeuroshillingCampaign) -> list[str]:
    """The accounts that will play, or the refusal explaining why none can."""
    accounts = await repository.list_campaign_accounts(campaign.campaign_id)
    playing = [item for item in accounts if item.state == "active" and not item.is_reserve]
    if len(playing) < settings.neuroshilling.min_accounts:
        raise NeuroshillingConflictError(_NOT_ENOUGH_ACCOUNTS)
    _roles, steps = await repository.load_scenario(campaign.campaign_id)
    staffed = {item.role_id for item in playing if item.role_id is not None}
    if any(step.role_id not in staffed for step in steps):
        raise NeuroshillingConflictError(_ROLE_WITHOUT_ACCOUNT)
    # Reserve accounts are claimed too: they are this campaign's substitution pool, and
    # letting another feature take one between the check and the substitution is how a
    # replacement lands on a session warming is already driving.
    return [item.account_id for item in accounts if item.state == "active"]


async def _claim_accounts(campaign_id: str, account_ids: list[str]) -> None:
    """Take every account for this campaign, or take none and refuse.

    Both neurocomment questions are asked FIRST and from the database, because that
    feature never writes the registry: an account serving an active campaign there, or
    standing as the running listener, is busy in a way no in-memory claim would show.
    Reading them before the loop is also what keeps the loop itself free of ``await`` —
    from the first claim to the last there is no suspension point, so a second start
    cannot interleave with this one.

    Every roster account's lifecycle lock is held across the reads AND the claims,
    because ``start_neurocomment`` commits its listener row under that same lock. The
    two starts therefore serialise per account, and whichever runs second sees what the
    first published: the listener row here, the registry claim there. Without the locks
    both read "free" — a database row and an in-memory claim are two publication points
    with awaits between them, so neither read covers the other's write.

    The ids are taken in sorted order so that two starts over overlapping rosters take
    the locks in the same order and cannot each wait on what the other holds. They are
    already distinct — ``(campaign_id, account_id)`` is the roster's primary key — which
    matters because these locks are plain ``asyncio.Lock``s and do not re-enter.
    """
    from services.warming import account_lock  # noqa: PLC0415 - avoids an import cycle

    async with AsyncExitStack() as locks:
        for account_id in sorted(account_ids):
            await locks.enter_async_context(account_lock(account_id))
        serving = await list_active_campaign_account_names()
        if any(account_id in serving for account_id in account_ids):
            raise NeuroshillingConflictError(_ACCOUNT_BUSY)
        listener = await _running_listener_account_id()
        if listener is not None and listener in account_ids:
            raise NeuroshillingConflictError(_ACCOUNT_IS_LISTENER)
        taken: list[str] = []
        for account_id in account_ids:
            if _account_owner.try_claim(account_id, _OWNER, campaign_id) is not None:
                for held in taken:
                    _account_owner.release(held, _OWNER, campaign_id)
                raise NeuroshillingConflictError(_ACCOUNT_BUSY)
            taken.append(account_id)


async def _running_listener_account_id() -> str | None:
    """The account the neurocomment listener is subscribed with, or ``None``.

    A remembered-but-PAUSED listener answers ``None``, the same reading
    ``start_warming`` takes of the same two columns: the operator switched that runtime
    off, so the session is free until they switch it back on — and at that moment
    ``start_neurocomment`` is the half that refuses.

    Read from the database rather than from neurocomment's in-process owner because the
    listener is not a holder in ``services._account_owner`` (see that module's note on
    why), so these columns are the only record of it that survives a restart and that
    exists before neurocomment's own startup reconciliation has run.
    """
    if not await get_listener_running():
        return None
    return await get_listener_account_id()


def _release_campaign(campaign_id: str) -> None:
    """Give back every account this campaign holds, and only the ones it holds.

    Read off the registry rather than off the roster: a save that changed the roster
    mid-run would otherwise leave the removed account claimed until a restart.
    """
    for account_id, owner in _account_owner.owners().items():
        if owner == _OWNER and _account_owner.holder_of(account_id) == campaign_id:
            _account_owner.release(account_id, _OWNER, campaign_id)


def _spawn(campaign_id: str, run_id: str, generation: int) -> None:
    task = asyncio.create_task(_run(campaign_id, run_id, generation))
    _TASKS[campaign_id] = task
    task.add_done_callback(lambda done: _forget(campaign_id, done))


def _forget(campaign_id: str, task: asyncio.Task[None]) -> None:
    """Drop the finished task and give back a roster its settlement could not release.

    A Stop whose drain expired writes the terminal row WITHOUT releasing, because the
    task was still unwinding and may hold a dispatch in flight; this is where those
    accounts come back. Nothing to do when a newer run already owns the map entry — the
    roster is that run's now — and the release is a no-op on the ordinary path, where
    ``_settle`` handed the accounts back before the task ended. A start that has claimed
    the roster but not yet published its task is the same case one moment earlier, and
    ``start_in_flight`` is what tells the two apart.
    """
    if _TASKS.get(campaign_id) is not task:
        return
    del _TASKS[campaign_id]
    if not _state.start_in_flight(campaign_id):
        _release_campaign(campaign_id)


async def _run(campaign_id: str, run_id: str, generation: int) -> None:
    """One background pass, fenced by its generation and settled exactly once."""
    status: NeuroshillingStatus = "done"
    error: str | None = None
    try:
        with _seams.run_scope(lambda: _state.run_is_current(campaign_id, generation)):
            await engine.run_campaign(campaign_id, run_id)
    except asyncio.CancelledError:
        # Nothing is settled from here, by design. A Stop writes the terminal row after
        # its own bounded drain, and a process shutdown deliberately leaves the campaign
        # ``running`` so boot reconciliation resumes THIS run_id rather than losing the
        # half-played dialogue sitting in somebody's chat.
        raise
    except _seams.NeuroshillingRunRevokedError:
        # Stop reached the fence instead of the cancellation; the stop path settles.
        return
    except Exception as exc:
        logger.exception("neuroshilling run failed for %s", campaign_id)
        status, error = "failed", type(exc).__name__
    await _settle(campaign_id, run_id, status, error)


async def _settle(
    campaign_id: str,
    run_id: str | None,
    status: NeuroshillingStatus,
    error: str | None,
    *,
    release: bool = True,
) -> None:
    """Write the terminal row and give the accounts back.

    ``claim_settlement`` is what fences it: a settle is refused once a NEWER run owns
    the campaign, which is what stops a late finisher writing ``done`` over its
    successor's ``running``.

    A ``None`` run id is the one case with nothing to fence against — a live row whose
    ``run_id`` never made it to disk — and it settles unconditionally so the campaign
    cannot be stuck ``running`` forever.

    ``release=False`` is the expired-drain case: the run task is still unwinding and may
    have a dispatch on the wire holding that account's lock, so handing the roster back
    now would let another feature claim a session with a call in flight. ``_forget``
    gives it back when the task finally ends.
    """
    if run_id is not None and not _state.claim_settlement(campaign_id, run_id):
        return
    if release:
        _release_campaign(campaign_id)
    await repository.set_run_state(campaign_id, status, run_id=None, last_error=error)
    await log_event(
        "ERROR" if status == "failed" else "INFO",
        "neuroshilling_run_failed" if status == "failed" else "neuroshilling_run_stopped",
        extra={"campaign_id": campaign_id} | ({"error_type": error} if error else {}),
    )


async def stop_campaign(campaign_id: str) -> NeuroshillingRunStatus | None:
    """Stop a run for real. ``None`` means no such campaign; stopping an idle one is a no-op.

    Idempotent rather than a 409: by the time the operator's click lands the run may
    have finished a second ago, and answering "conflict" to that is noise rather than
    information.
    """
    campaign = await repository.fetch_campaign(campaign_id)
    if campaign is None:
        return None
    if campaign.status not in _LIVE_STATUSES:
        return await run_status(campaign_id)
    _state.revoke_run(campaign_id)
    await repository.set_run_state(campaign_id, "stopping", run_id=campaign.run_id)
    drained = await _drain(campaign_id)
    fresh = await repository.fetch_campaign(campaign_id)
    if fresh is not None and fresh.status in _LIVE_STATUSES:
        # The task is gone, or is taking longer to unwind than the drain allows. Either
        # way it has been fenced and can publish nothing more, so the operator gets a
        # settled campaign now rather than a row that says "stopping" indefinitely. The
        # roster is only handed back when the task really did unwind.
        await _settle(campaign_id, fresh.run_id, "done", None, release=drained)
    return await run_status(campaign_id)


async def _drain(campaign_id: str) -> bool:
    """Cancel the run task and wait a bounded moment. ``False`` = still unwinding."""
    task = _TASKS.get(campaign_id)
    if task is None or task.done():
        return True
    task.cancel()
    done, _pending = await asyncio.wait({task}, timeout=settings.neuroshilling.stop_drain_seconds)
    return bool(done)


async def reconcile_neuroshilling_on_startup() -> None:
    """Resume what the previous process was playing — with the SAME run id.

    Runs AFTER warming's reconciliation. Neuroshilling TAKES accounts rather than
    resuming its own, and warming WRITES the ownership registry as it restores, so a
    claim taken before that would be taken against an empty map.

    The alternative — mark every interrupted campaign failed — was rejected: half a
    staged conversation left sitting in a stranger's chat is worse than finishing it.
    But either way the operator needs a visible line, which is why the resume logs one.
    """
    _account_owner.release_owner(_OWNER)
    for campaign in await repository.list_live_campaigns():
        await _resume(campaign)


async def _resume(campaign: NeuroshillingCampaign) -> None:
    campaign_id = campaign.campaign_id
    if campaign.status == "stopping":
        # A human already switched this one off, and ``stopping`` is a LIVE status, so
        # it arrives here with everything else. Re-claiming it would flip the row back
        # to ``running`` and put the fleet into real chats after an operator said no.
        # The row is live only because the previous process died inside
        # ``stop_campaign`` — a whole ``stop_drain_seconds`` wide, and indefinitely wide
        # if it died at the flip — so the restart finishes that Stop instead.
        await _settle(campaign_id, campaign.run_id, "done", None)
        return
    if campaign.run_id is None:
        await repository.set_run_state(
            campaign_id,
            "failed",
            run_id=None,
            last_error="RunIdMissing",
        )
        return
    # Settle first, then resume. A row still ``pending`` is a dispatch that never
    # finished or one whose outcome nobody learnt; it becomes ``failed`` WITHOUT being
    # deleted, so it goes on occupying its key and the resumed pass skips that step
    # instead of sending it a second time.
    settled = await repository.fail_pending_messages(campaign.run_id)
    played = await repository.list_journalled_steps(campaign.run_id)
    account_ids = [
        item.account_id
        for item in await repository.list_campaign_accounts(campaign_id)
        if item.state == "active"
    ]
    try:
        await _claim_accounts(campaign_id, account_ids)
    except NeuroshillingConflictError:
        # Half a campaign is worse than none: an account another feature took while we
        # were down means this run cannot be the run it was, so it is failed outright.
        await repository.set_run_state(
            campaign_id,
            "failed",
            run_id=None,
            last_error="AccountBusy",
        )
        await log_event("ERROR", "neuroshilling_run_failed", extra={"campaign_id": campaign_id})
        return
    generation = _state.begin_run(campaign_id, campaign.run_id)
    await repository.set_run_state(campaign_id, "running", run_id=campaign.run_id)
    await log_event(
        "WARNING",
        "neuroshilling_run_resumed",
        extra={"campaign_id": campaign_id, "settled": settled, "played": len(played)},
    )
    _spawn(campaign_id, campaign.run_id, generation)


async def shutdown_neuroshilling_on_shutdown() -> None:
    """Fence and drain every run, leaving the campaign rows exactly as they are.

    Drains BEFORE the Telegram pool is torn down (``main.lifespan``): these tasks hold
    pooled clients, and closing the pool under a live dispatch blows it up mid-handshake.

    The rows stay ``running`` on purpose — that is the signal boot reconciliation reads
    to pick the same run up again.
    """
    tasks = list(_TASKS.values())
    for campaign_id in list(_TASKS):
        _state.revoke_run(campaign_id)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(set(tasks), timeout=settings.neuroshilling.stop_drain_seconds)
    _account_owner.release_owner(_OWNER)


def reset_for_tests() -> None:
    _TASKS.clear()
