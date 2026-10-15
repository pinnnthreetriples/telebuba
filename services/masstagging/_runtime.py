"""Run lifecycle: start, continue, pause, boot reconciliation, shutdown — and ownership.

The shape of ``services.chat_broadcast._runtime``: claim → run id → one background task
→ settle exactly once, Pause as a generation bump plus a bounded drain, a restart that
resumes the SAME run id.

* Accounts another feature drives, spam-blocked and dead ones are LEFT OUT rather than
  refusing the start; Start refuses only when none can work.
* Pause, a stop on floods and a stop for lack of accounts are CONTINUED with their run
  id: the list remembers who is tagged. «Запустить снова» after a finished campaign
  mints a new run and lays the list out again.
* A story already handed to Telegram when the process died is never posted again: its
  people end ``failed`` / ``unconfirmed``; anyone else mid-step goes back to the queue.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING
from uuid import uuid4

from core.config import settings
from core.db import list_warming_account_ids
from core.logging import log_event
from core.repositories import masstagging as repository
from core.repositories.neurocomment import list_active_campaign_account_names
from services import _account_owner
from services.masstagging import _coordinator, _fleet, _seams, _state
from services.masstagging._errors import (
    CAMPAIGN_CHANGED,
    CAMPAIGN_RUNNING,
    NO_ACCOUNTS,
    NO_FREE_ACCOUNTS,
    NO_MEDIA,
    NO_TARGETS,
    MasstaggingConflictError,
)
from services.masstagging.campaigns import LIVE_STATUSES, missing_of, settings_of
from services.neuroshilling._listener_lookup import running_listener_account_id

if TYPE_CHECKING:
    from schemas.masstagging_records import CampaignRecord, MasstaggingRunStatus

logger = logging.getLogger(__name__)

_OWNER = "masstagging"
_RESUMABLE = frozenset({"paused", "failed"})
_TASKS: dict[str, asyncio.Task[None]] = {}
# What a run's outcome makes of the campaign: its status and the code the board words.
_SETTLED: dict[str, tuple[MasstaggingRunStatus, str | None]] = {
    "done": ("done", None),
    "volume": ("paused", "volume"),
    "floods": ("failed", "floods"),
    "no_accounts": ("failed", "no_accounts"),
}
_REFUSALS = {"no_accounts": NO_ACCOUNTS, "no_targets": NO_TARGETS, "no_media": NO_MEDIA}


async def start_campaign(campaign_id: str, *, expected_updated_at: str | None = None) -> bool:
    """Start, continue, or start again. ``False`` means no such campaign."""
    if not _state.try_claim_start(campaign_id):
        raise MasstaggingConflictError(CAMPAIGN_RUNNING)
    resume = False
    try:
        record = await repository.fetch_campaign(campaign_id)
        if record is None:
            return False
        if record.run_status in LIVE_STATUSES:
            raise MasstaggingConflictError(CAMPAIGN_RUNNING)
        if expected_updated_at is not None and record.updated_at != expected_updated_at:
            raise MasstaggingConflictError(CAMPAIGN_CHANGED)
        usable = await _launchable(record)
        claimed = await claim_free(campaign_id, usable)
        if not claimed:
            raise MasstaggingConflictError(NO_FREE_ACCOUNTS)
        resume = record.run_status in _RESUMABLE and record.run_id is not None
        run_id = record.run_id if resume and record.run_id else uuid4().hex
        await _publish(record, run_id, resume=resume)
    finally:
        _state.finish_start(campaign_id)
    await log_event(
        "INFO", "masstagging_run_started", extra={"campaign_id": campaign_id, "resumed": resume}
    )
    return True


async def _launchable(record: CampaignRecord) -> list[str]:
    """The accounts that may work, or the refusal explaining why the campaign cannot."""
    parsed = settings_of(record)
    roster = await repository.list_roster(record.campaign_id)
    fleet = await _fleet.fleet_of(roster)
    for missing in missing_of(parsed, roster, fleet):
        if missing in _REFUSALS:
            raise MasstaggingConflictError(_REFUSALS[missing])
    usable = [account_id for account_id in roster if fleet[account_id].usable]
    if not usable:
        raise MasstaggingConflictError(NO_FREE_ACCOUNTS)
    return usable


async def _publish(record: CampaignRecord, run_id: str, *, resume: bool) -> None:
    campaign_id = record.campaign_id
    now = _seams.now()
    fields: dict[str, object] = {
        "last_error": None,
        "finished_unix": None,
        "rest_until_unix": None,
        "session_unix": now,
    }
    if not resume:
        fields["started_unix"] = now
    try:
        generation = _state.begin_run(campaign_id, run_id)
        changed = await repository.set_status(
            campaign_id, "running", expected_updated_at=record.updated_at, run_id=run_id, **fields
        )
    except BaseException:
        _state.abandon_run(campaign_id, run_id)
        release_campaign(campaign_id)
        raise
    if not changed:
        _state.abandon_run(campaign_id, run_id)
        release_campaign(campaign_id)
        raise MasstaggingConflictError(CAMPAIGN_CHANGED)
    if resume:
        await settle_interrupted(campaign_id)
    _spawn(campaign_id, run_id, generation, fresh=not resume)


async def claim_free(campaign_id: str, account_ids: list[str]) -> list[str]:
    """Take every account no other feature is driving; return the ones taken.

    Each account's lifecycle lock is held across the reads and the claims, the way
    ``services.chat_broadcast._runtime.claim_free`` holds them.
    """
    from services.neurocomment import _discovery_state  # noqa: PLC0415 - import cycle
    from services.warming import account_lock  # noqa: PLC0415 - avoids an import cycle

    # Warming's channel search holds a lent account's lock for minutes: skip it, as
    # every other busy account is skipped, rather than wait the search out.
    account_ids = [a for a in account_ids if not _account_owner.is_lent(a)]
    async with AsyncExitStack() as locks:
        for account_id in sorted(set(account_ids)):
            await locks.enter_async_context(account_lock(account_id))
        busy = set(await list_active_campaign_account_names())
        busy |= set(await list_warming_account_ids())
        if (listener := await running_listener_account_id()) is not None:
            busy.add(listener)
        taken: list[str] = []
        for account_id in account_ids:
            if account_id in busy or _discovery_state.account_busy(account_id):
                continue
            if _account_owner.try_claim(account_id, _OWNER, campaign_id) is None:
                taken.append(account_id)
        return taken


def release_campaign(campaign_id: str) -> None:
    for account_id, owner in _account_owner.owners().items():
        if owner == _OWNER and _account_owner.holder_of(account_id) == campaign_id:
            _account_owner.release(account_id, _OWNER, campaign_id)


async def settle_interrupted(campaign_id: str) -> None:
    """A story handed to Telegram before the stop is never posted again; the rest requeue."""
    targets = await repository.list_targets(campaign_id)
    working = [t for t in targets if t.state == "working"]
    sent = [t.target_key for t in working if t.reason == "unconfirmed"]
    now = _seams.now()
    await repository.update_targets(
        campaign_id, sent, at=now, expect_states=("working",), state="failed"
    )
    await repository.update_targets(
        campaign_id,
        [t.target_key for t in working if t.reason != "unconfirmed"],
        at=now,
        expect_states=("working",),
        state="queued",
        reason=None,
        account_id=None,
    )


def _spawn(campaign_id: str, run_id: str, generation: int, *, fresh: bool) -> None:
    task = asyncio.create_task(_run(campaign_id, run_id, generation, fresh=fresh))
    _TASKS[campaign_id] = task
    task.add_done_callback(lambda done: _forget(campaign_id, done))


def _forget(campaign_id: str, task: asyncio.Task[None]) -> None:
    if _TASKS.get(campaign_id) is not task:
        return
    del _TASKS[campaign_id]
    if not _state.start_in_flight(campaign_id):
        release_campaign(campaign_id)


async def _run(campaign_id: str, run_id: str, generation: int, *, fresh: bool) -> None:
    status: MasstaggingRunStatus = "failed"
    error: str | None = None
    try:
        with _seams.run_scope(lambda: _state.run_is_current(campaign_id, generation)):
            outcome = await _coordinator.run_campaign(campaign_id, run_id, fresh=fresh)
        status, error = _SETTLED[outcome]
    except asyncio.CancelledError:
        raise
    except _seams.MasstaggingRunRevokedError:
        return
    except Exception as exc:
        logger.exception("masstagging run failed for %s", campaign_id)
        error = type(exc).__name__
    await settle(campaign_id, run_id, status, error)


async def settle(
    campaign_id: str,
    run_id: str | None,
    status: MasstaggingRunStatus,
    error: str | None,
    *,
    release: bool = True,
) -> None:
    """Write the terminal row once; a story left in flight is never posted again."""
    if run_id is not None and not _state.claim_settlement(campaign_id, run_id):
        return
    _state.forget_campaign(campaign_id)
    if release:
        release_campaign(campaign_id)
    await settle_interrupted(campaign_id)
    await repository.set_status(
        campaign_id, status, last_error=error, finished_unix=_seams.now(), rest_until_unix=None
    )
    extra: dict[str, object] = {"campaign_id": campaign_id}
    if status == "failed":
        await log_event("ERROR", "masstagging_run_failed", extra=extra | {"reason": error})
    elif status == "paused":
        await log_event("INFO", "masstagging_run_paused", extra=extra | {"reason": error})
    else:
        await log_event("INFO", "masstagging_run_done", extra=extra)


async def stop_campaign(campaign_id: str) -> bool:
    """Pause for real; idempotent. ``False`` means no such campaign."""
    record = await repository.fetch_campaign(campaign_id)
    if record is None:
        return False
    if record.run_status not in LIVE_STATUSES:
        return True
    if not _state.revoke_run_if_current(campaign_id, record.run_id):
        return True
    await repository.set_status(campaign_id, "stopping")
    drained = await _drain(campaign_id)
    fresh = await repository.fetch_campaign(campaign_id)
    if fresh is not None and fresh.run_status in LIVE_STATUSES:
        await settle(campaign_id, fresh.run_id, "paused", None, release=drained)
    return True


async def _drain(campaign_id: str) -> bool:
    task = _TASKS.get(campaign_id)
    if task is None or task.done():
        return True
    task.cancel()
    done, _pending = await asyncio.wait({task}, timeout=settings.masstagging.stop_drain_seconds)
    return bool(done)


async def reconcile_masstagging_on_startup() -> None:
    """Resume what the previous process was running, with the same run id."""
    _account_owner.release_owner(_OWNER)
    for record in await repository.list_live_campaigns():
        await _resume(record)


async def _resume(record: CampaignRecord) -> None:
    campaign_id = record.campaign_id
    if record.run_status == "stopping" or record.run_id is None:
        await settle(campaign_id, record.run_id, "paused", None)
        return
    await settle_interrupted(campaign_id)
    roster = await repository.list_roster(campaign_id)
    fleet = await _fleet.fleet_of(roster)
    usable = [account_id for account_id in roster if fleet[account_id].usable]
    if not await claim_free(campaign_id, usable):
        await settle(campaign_id, record.run_id, "failed", "no_accounts")
        return
    generation = _state.begin_run(campaign_id, record.run_id)
    await log_event("WARNING", "masstagging_run_resumed", extra={"campaign_id": campaign_id})
    _spawn(campaign_id, record.run_id, generation, fresh=False)


async def shutdown_masstagging_on_shutdown() -> None:
    """Fence and drain every run; the rows stay ``running`` for the next boot."""
    tasks = list(_TASKS.values())
    for campaign_id in list(_TASKS):
        _state.revoke_run(campaign_id)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(set(tasks), timeout=settings.masstagging.stop_drain_seconds)
    _account_owner.release_owner(_OWNER)


def reset_for_tests() -> None:
    _TASKS.clear()
