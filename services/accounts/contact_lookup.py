"""Operator-started phone-number lookup across selected accounts.

Mirror of :mod:`services.accounts.bulk_messages`: the operator uploads phone
numbers, one active run at a time resolves which of them are on Telegram, and the
found users are handed to the bulk-message form as recipients.

Optimised for the flood-sensitive ``contacts.ImportContacts`` RPC: numbers are
normalised and de-duplicated, spread round-robin across the selected accounts (a
phone is resolved by exactly one account, so the flood budget is not multiplied),
and each account's slice is sent in batches with a jittered pause between them. A
rate-limited account stops and its remaining numbers are reported skipped.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from contextlib import suppress
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from core.telegram_client import (
    TelegramAccountNotFoundError,
    TelegramReadError,
    execute_read,
)
from schemas.contact_lookup import ContactLookupJob, ContactLookupOutcome
from schemas.telegram_actions_contacts import CONTACT_LOOKUP_MAX_BATCH, LookupContactsByPhone

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from schemas.contact_lookup import ContactLookupRequest
    from schemas.telegram_actions_contacts import ContactLookupBatchResult, ContactLookupMatch

_jobs: dict[str, ContactLookupJob] = {}
_job_owners: dict[str, str] = {}
_pending: dict[str, tuple[ContactLookupRequest, dict[str, list[str]]]] = {}
_cancel_events: dict[str, asyncio.Event] = {}
_MAX_RETAINED_JOBS = 20
_BATCH_SIZE = CONTACT_LOOKUP_MAX_BATCH
_MIN_PHONE_DIGITS = 5
_MAX_PHONE_DIGITS = 15
logger = logging.getLogger(__name__)


def _normalize(phone: str) -> str | None:
    """Canonicalise one phone for de-dup and matching, or ``None`` if unusable.

    Strips every character Telethon's own ``parse_phone`` would drop anyway
    (spaces, brackets, dashes) so two spellings of one number collapse to a single
    lookup; a leading ``+`` is kept. The digit-count bounds only reject obvious
    junk — the gateway's ``parse_phone`` remains the authority on what actually
    reaches the RPC.
    """
    canon = re.sub(r"[^\d+]", "", phone.strip())
    digits = canon.lstrip("+")
    if not digits.isdigit() or not _MIN_PHONE_DIGITS <= len(digits) <= _MAX_PHONE_DIGITS:
        return None
    return canon


def _normalize_unique(phones: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in phones:
        canon = _normalize(raw)
        if canon is None or canon in seen:
            continue
        seen.add(canon)
        out.append(canon)
    return out


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def start_contact_lookup_job(data: ContactLookupRequest, owner_user_id: str) -> ContactLookupJob:
    """Normalise + distribute the numbers, then accept one run; a second is refused."""
    if any(job.status == "running" for job in _jobs.values()):
        msg = "contact_lookup_run_active"
        raise ValueError(msg)
    phones = _normalize_unique(data.phones)
    if not phones:
        msg = "no valid phones"
        raise ValueError(msg)
    for old_id, old_job in list(_jobs.items()):
        if len(_jobs) < _MAX_RETAINED_JOBS:
            break
        if old_job.status != "running":
            del _jobs[old_id]
            del _job_owners[old_id]
    distribution: dict[str, list[str]] = {account_id: [] for account_id in data.account_ids}
    for index, phone in enumerate(phones):
        account_id = data.account_ids[index % len(data.account_ids)]
        distribution[account_id].append(phone)
    job = ContactLookupJob(
        job_id=uuid4().hex,
        status="running",
        total=len(phones),
        completed=0,
        results=[],
    )
    _jobs[job.job_id] = job
    _job_owners[job.job_id] = owner_user_id
    _pending[job.job_id] = (data, distribution)
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


def _outcome(account_id: str, phone: str, match: ContactLookupMatch | None) -> ContactLookupOutcome:
    if match is None:
        return ContactLookupOutcome(phone=phone, account_id=account_id, status="not_found")
    display_name = " ".join(part for part in (match.first_name, match.last_name) if part) or None
    return ContactLookupOutcome(
        phone=phone,
        account_id=account_id,
        status="found",
        user_id=match.user_id,
        username=match.username,
        display_name=display_name,
    )


def _skip_remaining(
    account_id: str,
    batches: list[list[str]],
    job: ContactLookupJob,
    *,
    error_code: str,
    retry_after: int | None,
) -> None:
    for batch in batches:
        for phone in batch:
            job.results.append(
                ContactLookupOutcome(
                    phone=phone,
                    account_id=account_id,
                    status="skipped",
                    error_code=error_code,
                    retry_after_seconds=retry_after,
                )
            )
            job.completed += 1


async def _run_account_lookup(
    account_id: str,
    phones: list[str],
    data: ContactLookupRequest,
    job: ContactLookupJob,
    cancel_event: asyncio.Event,
) -> None:
    batches = list(_chunks(phones, _BATCH_SIZE))
    for batch_index, batch in enumerate(batches):
        if cancel_event.is_set():
            return
        if job.completed:
            delay = random.uniform(  # noqa: S311  # nosec B311 - timing jitter, not a secret
                data.min_delay_seconds, data.max_delay_seconds
            )
            if delay:
                with suppress(TimeoutError):
                    await asyncio.wait_for(cancel_event.wait(), timeout=delay)
            if cancel_event.is_set():
                return
        try:
            result = await execute_read(account_id, LookupContactsByPhone(phones=batch))
        except TelegramReadError as exc:
            # Only a rate limit carries a wait; any other read error still stops this
            # account, because the next batch would fail the same way.
            _skip_remaining(
                account_id,
                batches[batch_index:],
                job,
                error_code=_read_error_code(exc),
                retry_after=exc.seconds,
            )
            return
        except TelegramAccountNotFoundError:
            _skip_remaining(
                account_id,
                batches[batch_index:],
                job,
                error_code="account_not_found",
                retry_after=None,
            )
            return
        except Exception as exc:  # noqa: BLE001 - one account must not abort the run
            logger.warning("contact lookup batch failed: %s", type(exc).__name__)
            _skip_remaining(
                account_id, batches[batch_index:], job, error_code="failed", retry_after=None
            )
            return
        batch_result = cast("ContactLookupBatchResult", result)
        found = {match.phone: match for match in batch_result.matches}
        for phone in batch:
            job.results.append(_outcome(account_id, phone, found.get(phone)))
            job.completed += 1


async def run_contact_lookup_job(job_id: str) -> None:
    """Resolve each account's slice in batches; found users end up in the results."""
    data, distribution = _pending[job_id]
    job = _jobs[job_id]
    cancel_event = _cancel_events[job_id]
    try:
        for account_id, phones in distribution.items():
            if cancel_event.is_set():
                break
            if phones:
                await _run_account_lookup(account_id, phones, data, job, cancel_event)
    finally:
        job.status = (
            "cancelled" if cancel_event.is_set() and job.completed < job.total else "completed"
        )
        _pending.pop(job_id, None)
        _cancel_events.pop(job_id, None)
