"""Operator-started phone-number lookup across selected accounts.

Mirror of :mod:`services.accounts.bulk_messages`: the operator uploads phone
numbers, one active run at a time resolves which of them are on Telegram, and the
found users are handed to the bulk-message form as recipients.

Optimised for the flood-sensitive ``contacts.ImportContacts`` RPC: numbers are
normalised and de-duplicated, spread round-robin across the selected accounts (a
phone is resolved by exactly one account, so the flood budget is not multiplied),
and each account's share is sent in small batches with a jittered pause between
its own calls. Every number submitted counts against the account's UTC-day import
allowance (``outreach_daily_usage``), so a run never gives an account more than
it has left; numbers no account has room for are reported ``daily_limit``. A
rate-limited account (a FloodWait, or numbers Telegram deferred via
``retry_contacts``) stops and its remaining numbers are reported skipped; a
silently limited one hands its numbers on (see ``_contact_lookup_lanes``).

Account protection (``_outreach_protection``): accounts resting after a limit,
spam-blocked or flooded in the last day are left out before the run plans
anything; a young account's allowance is half; a rate-limited account starts its
rest, and once enough of them did the run skips every number still queued.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import unicodedata
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from core.repositories.outreach import (
    add_outreach_usage,
    list_accounts_with_contact_matches,
    list_outreach_usage,
    load_outreach_limits,
)
from core.telegram_client import (
    TelegramAccountNotFoundError,
    TelegramReadError,
    execute_read,
)
from schemas.contact_lookup import ContactLookupJob
from schemas.outreach_limits import OutreachDailyUsage
from schemas.outreach_protection import RUN_STOPPED_CODE
from schemas.telegram_actions_contacts import LookupContactsByPhone
from services.accounts._contact_lookup_lanes import (
    Lane,
    hand_over_silent_lane,
    looks_silently_limited,
    plan_lanes,
    record_batch,
    skip_phones,
    stop_lane,
)
from services.accounts._contact_rechecks import (
    load_contact_rechecks,
    place_rechecks,
    settle_rechecks,
    summarize_contact_rechecks,
)
from services.accounts._outreach_protection import (
    halve,
    note_limit,
    skipped_accounts,
    young_account_ids,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from schemas.contact_lookup import ContactLookupRequest, ContactRecheckSummary
    from schemas.outreach_limits import OutreachLimits
    from schemas.outreach_protection import OutreachProtection
    from schemas.telegram_actions_contacts import ContactLookupBatchResult

_jobs: dict[str, ContactLookupJob] = {}
_job_owners: dict[str, str] = {}
_pending: dict[str, tuple[ContactLookupRequest, list[str]]] = {}
_cancel_events: dict[str, asyncio.Event] = {}
_MAX_RETAINED_JOBS = 20
# Formatting separators, including the Unicode dashes (U+2010..U+2015, U+2212) a copied
# number may carry.
_PHONE_SEPARATORS = re.compile(r"[\s().\-\u2010-\u2015\u2212]")
# The digit bounds only reject obvious junk; parse_phone in the gateway stays the authority.
_PHONE = re.compile(r"\+?[0-9]{5,15}")
logger = logging.getLogger(__name__)


def _normalize(phone: str) -> str | None:
    """Canonicalise one phone for matching, or ``None`` if unusable.

    Strips only formatting: separators (spaces, brackets, dots, dashes) and the
    invisible direction marks a number copied from a contacts app is wrapped in; a
    leading ``+`` is kept. Anything else — letters, commas, a second ``+`` — rejects
    the entry, so a comma-joined pair is never fused into one fake number.
    """
    visible = "".join(char for char in phone if unicodedata.category(char) != "Cf")
    canon = _PHONE_SEPARATORS.sub("", visible)
    return canon if _PHONE.fullmatch(canon) else None


def _normalize_unique(phones: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in phones:
        canon = _normalize(raw)
        if canon is None:
            continue
        # parse_phone drops the "+", so "+7999…" and "7999…" are one lookup.
        key = canon.removeprefix("+")
        if key in seen:
            continue
        seen.add(key)
        out.append(canon)
    return out


@dataclass(frozen=True)
class _Pacing:
    batch_size: int
    min_delay_seconds: float
    max_delay_seconds: float
    silent_limit_streak: int
    protection: OutreachProtection

    @classmethod
    def resolve(cls, data: ContactLookupRequest, limits: OutreachLimits) -> _Pacing:
        """The run's own pacing where the form sent it, the saved limits otherwise."""
        if data.min_delay_seconds is None or data.max_delay_seconds is None:
            delays = (limits.lookup_min_delay_seconds, limits.lookup_max_delay_seconds)
        else:
            delays = (data.min_delay_seconds, data.max_delay_seconds)
        return cls(
            batch_size=data.batch_size or limits.lookup_batch_size,
            min_delay_seconds=delays[0],
            max_delay_seconds=delays[1],
            silent_limit_streak=limits.silent_limit_streak,
            protection=data.protection or limits.protection,
        )


def start_contact_lookup_job(data: ContactLookupRequest, owner_user_id: str) -> ContactLookupJob:
    """Normalise the numbers, then accept one run; a second is refused.

    The numbers are spread over the accounts when the run starts, against each
    account's allowance left at that moment.
    """
    if any(job.status == "running" for job in _jobs.values()):
        msg = "contact_lookup_run_active"
        raise ValueError(msg)
    phones = _normalize_unique(data.phones)
    # No numbers at all is a run of the due "not found" re-checks only.
    if not phones and (data.phones or data.recheck_not_found is False):
        msg = "no valid phones"
        raise ValueError(msg)
    for old_id, old_job in list(_jobs.items()):
        if len(_jobs) < _MAX_RETAINED_JOBS:
            break
        if old_job.status != "running":
            del _jobs[old_id]
            del _job_owners[old_id]
    job = ContactLookupJob(
        job_id=uuid4().hex,
        status="running",
        total=len(phones),
        completed=0,
        results=[],
    )
    _jobs[job.job_id] = job
    _job_owners[job.job_id] = owner_user_id
    _pending[job.job_id] = (data, phones)
    _cancel_events[job.job_id] = asyncio.Event()
    return job.model_copy(deep=True)


def get_contact_lookup_job(job_id: str, owner_user_id: str) -> ContactLookupJob | None:
    if _job_owners.get(job_id) != owner_user_id:
        return None
    job = _jobs.get(job_id)
    return job.model_copy(deep=True) if job is not None else None


def get_active_contact_lookup_job(owner_user_id: str) -> ContactLookupJob | None:
    for job_id, job in _jobs.items():
        if _job_owners[job_id] == owner_user_id and job.status == "running":
            return job.model_copy(deep=True)
    return None


def get_latest_contact_lookup_job(owner_user_id: str) -> ContactLookupJob | None:
    for job_id in reversed(_jobs):
        if _job_owners[job_id] == owner_user_id:
            return _jobs[job_id].model_copy(deep=True)
    return None


def cancel_contact_lookup_job(job_id: str, owner_user_id: str) -> ContactLookupJob | None:
    """Stop after the current batch; a pacing wait is interrupted at once."""
    if _job_owners.get(job_id) != owner_user_id:
        return None
    job = _jobs.get(job_id)
    if job is None:
        return None
    event = _cancel_events.get(job_id)
    if event is not None:
        event.set()
    return job.model_copy(deep=True)


async def get_contact_rechecks() -> ContactRecheckSummary:
    """The "not found" numbers waiting for another account's re-check."""
    return await summarize_contact_rechecks()


def _read_error_code(exc: TelegramReadError) -> str:
    """Map a gateway read error onto one stable code the UI already renders.

    The whole flood family arrives as ``kind == "flood_wait"``; its member is only
    distinguishable by the reason label the gateway formatted, so each limit keeps
    the same code the bulk-message screen uses.
    """
    if exc.kind == "flood_wait":
        reason = exc.reason
        if reason.startswith("FloodPremiumWait"):
            return "premium_wait"
        if reason.startswith("SlowModeWait"):
            return "slow_mode_wait"
        if reason.startswith("PeerFlood"):
            return "peer_flood"
        return "flood_wait"
    if exc.kind == "unavailable":
        return "unavailable"
    return "failed"


def _utc_day() -> str:
    return datetime.now(UTC).date().isoformat()


async def _pause(cancel_event: asyncio.Event, seconds: float) -> None:
    """Wait out an account's pause; a cancel interrupts it at once."""
    with suppress(TimeoutError):
        await asyncio.wait_for(cancel_event.wait(), timeout=seconds)


async def _check_batch(lane: Lane, batch: list[str], job: ContactLookupJob) -> bool:
    """Run one ``ImportContacts`` call for the lane; False once the lane is stopped."""
    # Counted before the call: a number Telegram saw counts even if the reply is lost.
    await add_outreach_usage(
        OutreachDailyUsage(account_id=lane.account_id, day=_utc_day(), contact_imports=len(batch))
    )
    try:
        result = await execute_read(lane.account_id, LookupContactsByPhone(phones=batch))
    except TelegramReadError as exc:
        # Only a rate limit carries a wait; any other read error still stops this
        # account, because the next batch would fail the same way.
        stop_lane(lane, batch, job, error_code=_read_error_code(exc), retry_after=exc.seconds)
        return False
    except TelegramAccountNotFoundError:
        stop_lane(lane, batch, job, error_code="account_not_found", retry_after=None)
        return False
    except Exception as exc:  # noqa: BLE001 - one account must not abort the run
        logger.warning("contact lookup batch failed: %s", type(exc).__name__)
        stop_lane(lane, batch, job, error_code="failed", retry_after=None)
        return False
    batch_result = cast("ContactLookupBatchResult", result)
    deferred = record_batch(lane, batch, batch_result, job)
    if batch_result.matches:
        await add_outreach_usage(
            OutreachDailyUsage(
                account_id=lane.account_id,
                day=_utc_day(),
                contact_matches=len(batch_result.matches),
            )
        )
    if deferred:
        # Telegram's import limit: the deferred numbers were never checked, and
        # the next batch would be deferred the same way.
        stop_lane(lane, deferred, job, error_code="flood_wait", retry_after=None)
        return False
    return True


async def _run_lanes(
    lanes: list[Lane],
    pacing: _Pacing,
    job: ContactLookupJob,
    cancel_event: asyncio.Event,
) -> None:
    """Always serve the lane whose own pause ends first; accounts never wait for each other."""
    loop = asyncio.get_running_loop()
    while not cancel_event.is_set():
        waiting = [lane for lane in lanes if lane.queue and not lane.stopped]
        if not waiting:
            return
        lane = min(waiting, key=lambda candidate: candidate.ready_at)
        delay = lane.ready_at - loop.time()
        if delay > 0:
            await _pause(cancel_event, delay)
            if cancel_event.is_set():
                return
        if not await _check_batch(lane, lane.take(pacing.batch_size), job):
            if lane.limit is not None:
                await _limited(lane, lanes, pacing.protection, job)
            continue
        if looks_silently_limited(lane, lanes, pacing.silent_limit_streak):
            hand_over_silent_lane(lane, lanes, job)
            continue
        lane.ready_at = loop.time() + random.uniform(  # noqa: S311  # nosec B311 - timing jitter, not a secret
            pacing.min_delay_seconds, pacing.max_delay_seconds
        )


async def _limited(
    lane: Lane, lanes: list[Lane], protection: OutreachProtection, job: ContactLookupJob
) -> None:
    """Start the lane's rest; past the threshold, skip every number still queued."""
    code, retry_after = lane.limit or ("", None)
    limited = sum(other.limit is not None for other in lanes)
    if not await note_limit(lane.account_id, code, retry_after, protection, limited):
        return
    for other in lanes:
        if not other.stopped:
            stop_lane(other, [], job, error_code=RUN_STOPPED_CODE, retry_after=None)


async def run_contact_lookup_job(job_id: str) -> None:
    """Check each account's share within its daily allowance; found users end up in the results."""
    data, phones = _pending[job_id]
    job = _jobs[job_id]
    cancel_event = _cancel_events[job_id]
    try:
        limits = await load_outreach_limits()
        pacing = _Pacing.resolve(data, limits)
        now = datetime.now(UTC)
        job.skipped_accounts = await skipped_accounts(data.account_ids, pacing.protection, now)
        left_out = {skipped.account_id for skipped in job.skipped_accounts}
        account_ids = [account_id for account_id in data.account_ids if account_id not in left_out]
        if not account_ids:
            for index, phone in enumerate(phones):
                why = job.skipped_accounts[index % len(job.skipped_accounts)]
                skip_phones(
                    why.account_id,
                    [phone],
                    job,
                    error_code=why.reason,
                    retry_after=why.retry_after_seconds,
                )
            return
        young = await young_account_ids(account_ids, pacing.protection, now)
        usage = await list_outreach_usage(account_ids, _utc_day())
        cap = data.daily_cap or limits.contact_imports_per_day
        allowances = {
            account_id: (halve(cap) if account_id in young else cap) - row.contact_imports
            for account_id, row in usage.items()
        }
        found_before = await list_accounts_with_contact_matches(account_ids)
        lanes, overflow = plan_lanes(phones, account_ids, allowances, found_before)
        for account_id, phone in overflow:
            skip_phones(account_id, [phone], job, error_code="daily_limit")
        recheck = limits.recipients.recheck_not_found
        if data.recheck_not_found is not None:
            recheck = data.recheck_not_found
        queued = await load_contact_rechecks()
        if recheck:
            # The operator's own numbers first; due re-checks take the room left over.
            job.total += len(place_rechecks(lanes, queued.values(), phones, datetime.now(UTC)))
        await _run_lanes(lanes, pacing, job, cancel_event)
        await settle_rechecks(job, queued, enabled=recheck, now=datetime.now(UTC))
    finally:
        job.status = (
            "cancelled" if cancel_event.is_set() and job.completed < job.total else "completed"
        )
        _pending.pop(job_id, None)
        _cancel_events.pop(job_id, None)
