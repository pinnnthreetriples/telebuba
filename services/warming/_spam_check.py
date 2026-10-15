"""Scheduled @SpamBot check — the cycle's spam step and the hand-off's precondition.

Warming used to ask @SpamBot only from quarantine, so an account that never hit a peer
flood was never asked at all, and "no reading" read as "clean" wherever a verdict was
consulted. Now the cycle asks on the first cycle of a stint and then about every
``spam_check_interval_hours`` (shifted per account within ±``spam_check_jitter_hours``),
and the hand-off to neurocomment cannot happen without a fresh ``clean``.

Telegram is reached only through ``_seams.refresh_spam_status`` — the lease-fenced seam
quarantine recovery uses — and nothing here runs from an API start: a pool start queues
loops, and each loop asks inside its own paced cycle.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import get_spam_status, record_spam_check_outcome
from core.logging import log_event
from schemas.warming import WarmingCycleResult, spam_block_known
from services.warming import _seams
from services.warming._state import _set_state

if TYPE_CHECKING:
    from schemas.spam_status import SpamStatusVerdict
    from schemas.warming import WarmingStateRecord

_OFFSET_SPAN = 2**64
_LIMITED_ERROR = "spam limited"


def _parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def check_offset_hours(account_id: str) -> float:
    """This account's fixed shift of the check interval, within ±jitter hours.

    Stable per account (a hash, not a draw) so the cadence does not wander cycle to
    cycle, while a fleet started together still spreads its checks over a day.
    """
    jitter = settings.warming.spam_check_jitter_hours
    digest = hashlib.sha256(f"spam-check:{account_id}".encode()).digest()
    fraction = int.from_bytes(digest[:8], "big") / _OFFSET_SPAN
    return (2 * fraction - 1) * jitter


def spam_check_due(
    cached: SpamStatusVerdict | None,
    record: WarmingStateRecord,
    now: datetime,
) -> bool:
    """True when this cycle must ask @SpamBot before doing anything else.

    No reading, a cached ``unknown`` or an unreadable last attempt ask now. So does the
    first cycle of a stint whose reading predates it and is past the cache TTL, and
    any reading older than the per-account interval.
    """
    if cached is None or cached.status == "unknown" or record.spam_unknown_streak > 0:
        return True
    checked = _parse_iso(cached.checked_at)
    if checked is None:
        return True
    age = now - checked
    started = _parse_iso(record.started_at)
    ttl = timedelta(hours=settings.warming.spam_status_ttl_hours)
    if started is not None and checked < started and age >= ttl:
        return True
    interval = settings.warming.spam_check_interval_hours + check_offset_hours(record.account_id)
    return age >= timedelta(hours=interval)


async def ask_spambot(account_id: str) -> SpamStatusVerdict:
    """Ask @SpamBot through the warming seam, reusing a fresh reading.

    A cached ``unknown`` ("being checked") is not a reading, so it is re-asked rather
    than served from the cache for the rest of its TTL.
    """
    cached = await get_spam_status(account_id)
    force = cached is not None and cached.status == "unknown"
    return await _seams.refresh_spam_status(account_id, force=force)


async def _quarantine_limited(
    account_id: str,
    now: datetime,
    *,
    run_id: str | None,
) -> WarmingCycleResult:
    """Send a limited account to quarantine — a recoverable hold, not ``error``."""
    next_run = now + timedelta(hours=settings.warming.quarantine_hours)
    write = await _set_state(
        account_id,
        "quarantine",
        last_event="spam_check_limited",
        next_run_at=next_run.isoformat(),
        heartbeat_at=now.isoformat(),
        last_error=_LIMITED_ERROR,
        expected_run_id=run_id,
    )
    if run_id is not None and not write.applied:
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="stale run")
    await log_event("WARNING", "warming_spam_check_limited", account_id=account_id)
    return WarmingCycleResult(account_id=account_id, status="skipped", detail=_LIMITED_ERROR)


async def _gate_spam_check(
    account_id: str,
    record: WarmingStateRecord | None,
    now: datetime,
    *,
    run_id: str | None,
) -> WarmingCycleResult | None:
    """The cycle's spam step on a fresh read of the cached reading; see ``_spam_step``."""
    if record is None:
        return None
    cached = await get_spam_status(account_id)
    gated, _asked = await _spam_step(account_id, record, cached, now, run_id=run_id)
    return gated


async def _spam_step(
    account_id: str,
    record: WarmingStateRecord,
    cached: SpamStatusVerdict | None,
    now: datetime,
    *,
    run_id: str | None,
) -> tuple[WarmingCycleResult | None, bool]:
    """The cycle's spam step: ask when due, quarantine on a new ``limited``.

    An unreadable answer adds one to the streak and lets the cycle go on — the next
    cycle asks again; a reading resets the streak. Returns the terminal result when
    the iteration must stop (else ``None``) and whether @SpamBot was asked, so the
    caller knows ``cached`` is no longer the stored reading.
    """
    if not spam_check_due(cached, record, now):
        return None, False
    force = cached is not None and cached.status == "unknown"
    verdict = await _seams.refresh_spam_status(account_id, force=force)
    readable = verdict.status != "unknown"
    if not readable or record.spam_unknown_streak > 0:
        streak = await record_spam_check_outcome(
            account_id,
            readable=readable,
            expected_run_id=run_id,
        )
        if streak is None:
            stale = WarmingCycleResult(account_id=account_id, status="skipped", detail="stale run")
            return stale, True
        if not readable:
            await log_event(
                "WARNING",
                "warming_spam_check_unreadable",
                account_id=account_id,
                extra={"streak": streak},
            )
    # A kept account's block is already known: its ``limited`` is no news, warming goes
    # on without DMs instead of re-entering the quarantine ladder it already finished.
    if verdict.status == "limited" and not spam_block_known(record):
        return await _quarantine_limited(account_id, now, run_id=run_id), True
    return None, True
