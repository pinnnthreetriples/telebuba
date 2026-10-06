"""Run lifecycle: start, continue, stop, boot reconciliation, shutdown — and ownership.

The shape of ``services.neuroshilling._runtime``: claim → run id → one background task
→ settle exactly once, Stop as a generation bump plus a bounded drain, a restart that
resumes the SAME run id. Two differences, both from the agreed design:

* Accounts another feature is driving are LEFT OUT rather than refusing the start; the
  board shows who has them. Start refuses only when no account is free at all.
* A stopped, stalled or failed run is CONTINUED with its run id: the journal says which
  steps each chat already got, so nothing is sent twice. Only a finished (or never
  started) campaign mints a new run and lays its chats out from scratch.
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
from core.repositories import chat_broadcast as repository
from core.repositories.neurocomment import list_active_campaign_account_names
from services import _account_owner
from services.chat_broadcast import _coordinator, _seams, _state
from services.chat_broadcast._context import usable_chain
from services.chat_broadcast._errors import (
    CAMPAIGN_CHANGED,
    CAMPAIGN_RUNNING,
    NO_ACCOUNTS,
    NO_FREE_ACCOUNTS,
    NO_MESSAGES,
    NO_TARGETS,
    ChatBroadcastConflictError,
)
from services.chat_broadcast._moves import ChatBroadcastStoppedOnErrorError
from services.chat_broadcast.campaigns import settings_of
from services.chat_broadcast.links import split_targets
from services.neuroshilling._listener_lookup import running_listener_account_id

if TYPE_CHECKING:
    from schemas.chat_broadcast import ChatBroadcastStatus
    from schemas.chat_broadcast_records import CampaignRecord

logger = logging.getLogger(__name__)

_OWNER = "chat_broadcast"
_LIVE_STATUSES = frozenset({"running", "stopping"})
_RESUMABLE = frozenset({"stopped", "stalled", "failed"})
_TASKS: dict[str, asyncio.Task[None]] = {}


async def start_campaign(campaign_id: str, *, expected_updated_at: str | None = None) -> bool:
    """Start, or continue, a run. ``False`` means no such campaign."""
    if not _state.try_claim_start(campaign_id):
        raise ChatBroadcastConflictError(CAMPAIGN_RUNNING)
    resume = False
    try:
        record = await repository.fetch_campaign(campaign_id)
        if record is None:
            return False
        if record.status in _LIVE_STATUSES:
            raise ChatBroadcastConflictError(CAMPAIGN_RUNNING)
        if expected_updated_at is not None and record.updated_at != expected_updated_at:
            raise ChatBroadcastConflictError(CAMPAIGN_CHANGED)
        roster = await _launchable_roster(record)
        resume = record.status in _RESUMABLE and record.run_id is not None and record.round > 0
        run_id = record.run_id if resume and record.run_id else uuid4().hex
        claimed = await claim_free(campaign_id, roster)
        if not claimed:
            raise ChatBroadcastConflictError(NO_FREE_ACCOUNTS)
        await _publish(record, run_id, resume=resume)
    finally:
        _state.finish_start(campaign_id)
    await log_event(
        "INFO", "chat_broadcast_run_started", extra={"campaign_id": campaign_id, "resumed": resume}
    )
    return True


async def _launchable_roster(record: CampaignRecord) -> list[str]:
    """The roster that may play, or the refusal explaining why the campaign cannot."""
    parsed = settings_of(record)
    if not usable_chain(parsed):
        raise ChatBroadcastConflictError(NO_MESSAGES)
    if parsed.target_mode == "list" and not split_targets(parsed.targets):
        raise ChatBroadcastConflictError(NO_TARGETS)
    roster = await repository.list_roster(record.campaign_id)
    active = [item.account_id for item in roster if item.state == "active"]
    if not active:
        raise ChatBroadcastConflictError(NO_ACCOUNTS)
    return active


async def _publish(record: CampaignRecord, run_id: str, *, resume: bool) -> None:
    campaign_id = record.campaign_id
    fields: dict[str, object] = {"last_error": None, "finished_unix": None}
    if not resume:
        fields |= {"round": 0, "rest_until_unix": None, "started_unix": _seams.now()}
        fields |= {"resumed_unix": None}
    try:
        generation = _state.begin_run(campaign_id, run_id)
        changed = await repository.set_status(
            campaign_id,
            "running",
            expected_updated_at=record.updated_at,
            run_id=run_id,
            **fields,
        )
    except BaseException:
        _state.abandon_run(campaign_id, run_id)
        release_campaign(campaign_id)
        raise
    if not changed:
        _state.abandon_run(campaign_id, run_id)
        release_campaign(campaign_id)
        raise ChatBroadcastConflictError(CAMPAIGN_CHANGED)
    _spawn(campaign_id, run_id, generation, refresh=resume)


async def claim_free(campaign_id: str, account_ids: list[str]) -> list[str]:
    """Take every roster account no other feature is driving; return the ones taken.

    Each account's lifecycle lock is held across the reads and the claims, the way
    ``services.neuroshilling._runtime._claim_accounts`` holds them, so a listener or
    warming start serialises with this one per account.
    """
    from services.neurocomment import _discovery_state  # noqa: PLC0415 - import cycle
    from services.warming import account_lock  # noqa: PLC0415 - avoids an import cycle

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


def _spawn(campaign_id: str, run_id: str, generation: int, *, refresh: bool) -> None:
    task = asyncio.create_task(_run(campaign_id, run_id, generation, refresh=refresh))
    _TASKS[campaign_id] = task
    task.add_done_callback(lambda done: _forget(campaign_id, done))


def _forget(campaign_id: str, task: asyncio.Task[None]) -> None:
    if _TASKS.get(campaign_id) is not task:
        return
    del _TASKS[campaign_id]
    if not _state.start_in_flight(campaign_id):
        release_campaign(campaign_id)


async def _run(campaign_id: str, run_id: str, generation: int, *, refresh: bool) -> None:
    status: ChatBroadcastStatus = "done"
    error: str | None = None
    try:
        with _seams.run_scope(lambda: _state.run_is_current(campaign_id, generation)):
            outcome = await _coordinator.run_campaign(campaign_id, run_id, refresh=refresh)
        status = "stalled" if outcome == "stalled" else "done"
    except asyncio.CancelledError:
        raise
    except _seams.ChatBroadcastRunRevokedError:
        return
    except ChatBroadcastStoppedOnErrorError as exc:
        status, error = "failed", str(exc)
    except Exception as exc:
        logger.exception("chat broadcast run failed for %s", campaign_id)
        status, error = "failed", type(exc).__name__
    await settle(campaign_id, run_id, status, error)


async def settle(
    campaign_id: str,
    run_id: str | None,
    status: ChatBroadcastStatus,
    error: str | None,
    *,
    release: bool = True,
) -> None:
    """Write the terminal row once; a pending send left behind becomes unconfirmed."""
    if run_id is not None and not _state.claim_settlement(campaign_id, run_id):
        return
    if release:
        release_campaign(campaign_id)
    if run_id is not None:
        await repository.unconfirm_pending(run_id)
    await repository.set_status(
        campaign_id, status, last_error=error, finished_unix=_seams.now(), rest_until_unix=None
    )
    extra: dict[str, object] = {"campaign_id": campaign_id}
    if status == "failed":
        await log_event("ERROR", "chat_broadcast_run_failed", extra=extra | {"error_type": error})
    elif status == "stalled":
        await log_event("ERROR", "chat_broadcast_run_stalled", extra=extra)
    elif status == "stopped":
        await log_event("INFO", "chat_broadcast_run_stopped", extra=extra)
    else:
        await log_event("INFO", "chat_broadcast_run_finished", extra=extra)


async def stop_campaign(campaign_id: str) -> bool:
    """Stop for real; idempotent. ``False`` means no such campaign."""
    record = await repository.fetch_campaign(campaign_id)
    if record is None:
        return False
    if record.status not in _LIVE_STATUSES:
        return True
    if not _state.revoke_run_if_current(campaign_id, record.run_id):
        return True
    await repository.set_status(campaign_id, "stopping")
    drained = await _drain(campaign_id)
    fresh = await repository.fetch_campaign(campaign_id)
    if fresh is not None and fresh.status in _LIVE_STATUSES:
        await settle(campaign_id, fresh.run_id, "stopped", None, release=drained)
    return True


async def _drain(campaign_id: str) -> bool:
    task = _TASKS.get(campaign_id)
    if task is None or task.done():
        return True
    task.cancel()
    done, _pending = await asyncio.wait({task}, timeout=settings.chat_broadcast.stop_drain_seconds)
    return bool(done)


async def reconcile_chat_broadcast_on_startup() -> None:
    """Resume what the previous process was running, with the same run id."""
    _account_owner.release_owner(_OWNER)
    for record in await repository.list_live_campaigns():
        await _resume(record)


async def _resume(record: CampaignRecord) -> None:
    campaign_id = record.campaign_id
    if record.status == "stopping":
        await settle(campaign_id, record.run_id, "stopped", None)
        return
    if record.run_id is None:
        await repository.set_status(campaign_id, "failed", last_error="RunIdMissing")
        return
    settled = await repository.unconfirm_pending(record.run_id)
    await repository.settle_interrupted(campaign_id)
    roster = await repository.list_roster(campaign_id)
    active = [item.account_id for item in roster if item.state == "active"]
    if not await claim_free(campaign_id, active):
        await repository.set_status(campaign_id, "stalled", last_error="AccountBusy")
        await log_event("ERROR", "chat_broadcast_run_stalled", extra={"campaign_id": campaign_id})
        return
    generation = _state.begin_run(campaign_id, record.run_id)
    await repository.set_status(campaign_id, "running", resumed_unix=_seams.now())
    await log_event(
        "WARNING",
        "chat_broadcast_run_resumed",
        extra={"campaign_id": campaign_id, "settled": settled},
    )
    _spawn(campaign_id, record.run_id, generation, refresh=False)


async def shutdown_chat_broadcast_on_shutdown() -> None:
    """Fence and drain every run; the rows stay ``running`` for the next boot."""
    tasks = list(_TASKS.values())
    for campaign_id in list(_TASKS):
        _state.revoke_run(campaign_id)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(set(tasks), timeout=settings.chat_broadcast.stop_drain_seconds)
    _account_owner.release_owner(_OWNER)


def reset_for_tests() -> None:
    _TASKS.clear()
