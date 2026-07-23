"""Transient per-account engine state — cooldowns after a flood/peer-flood.

The in-memory dicts below are the hot read path. The flood/peer-flood/slow-mode
cooldowns (``_COOLDOWN_UNTIL``, set via ``set_cooldown``) are additionally
mirrored to ``neurocomment_cooldowns`` and rehydrated at startup (#34), so a
just-flooded account stays parked across a process restart. The channel/challenge
back-offs stay in-memory only — they are recomputed each sweep and self-heal.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from core.repositories.neurocomment import load_active_cooldowns, persist_cooldown

# ponytail: single-process. A multi-process deployment would still need shared
# storage for the read path; the DB here is durability, not cross-process sharing.

# (account_id, channel) -> earliest UTC time it may comment again. channel=None is
# an account-wide cooldown (flood/peer-flood); a channel scopes it to that chat
# (slow-mode is per-chat, so it must not park the account everywhere).
_COOLDOWN_UNTIL: dict[tuple[str, str | None], datetime] = {}

# Deletion back-off (#131), keyed by channel — the periodic sweep trips it when too
# many of a channel's comments vanish. Mirrors the per-account cooldown above but
# channel-scoped, in-memory only (recomputed each sweep, self-healing on restart).
_CHANNEL_TRIPS: dict[str, int] = {}  # consecutive trips this process (escalation memory)
_CHANNEL_COOLDOWN_UNTIL: dict[str, datetime] = {}  # earliest UTC time to comment again
# Comment ids already counted toward a channel's deletion back-off, so the same
# vanished comments (they linger in the lookback window for hours) are never
# re-counted across cooldowns — that would walk one deletion episode to the cap.
_CHANNEL_COUNTED_DELETED: dict[str, set[int]] = {}

# Challenge back-off (#147), keyed by channel — K consecutive solver failures
# (pending → failed) trip an escalating cooldown that stops onboarding new accounts
# into the channel. Mirrors the deletion back-off above; in-memory, self-healing.
_CHALLENGE_FAILED: dict[str, int] = {}  # failures since the last trip (the K counter)
_CHALLENGE_TRIPS: dict[str, int] = {}  # consecutive trips this process (escalation memory)
_CHALLENGE_BACKOFF_UNTIL: dict[str, datetime] = {}  # earliest UTC time to onboard again


async def set_cooldown(account_id: str, until: datetime, channel: str | None = None) -> None:
    """Park ``(account, channel)`` until ``until`` (extends an existing, later cooldown).

    The in-memory map is updated first (so ``in_cooldown`` sees the deadline
    immediately and it is never lost if the durable write fails), then the row is
    persisted off the event loop via ``asyncio.to_thread`` — the single-worker
    loop must never block on the SQLite write during a flood storm. Only a
    genuinely-later deadline is written, matching the in-memory extend rule.
    """
    if until.tzinfo is None:
        # A naive datetime would ISO-serialize without an offset; the prune's
        # string comparison (``until <= now``) then breaks against aware rows.
        msg = "set_cooldown 'until' must be timezone-aware UTC"
        raise ValueError(msg)
    key = (account_id, channel)
    current = _COOLDOWN_UNTIL.get(key)
    if current is None or until > current:
        _COOLDOWN_UNTIL[key] = until
        await asyncio.to_thread(persist_cooldown, account_id, channel, until.isoformat())


def in_cooldown(account_id: str, now: datetime, channel: str | None = None) -> bool:
    """True while the account is cooled account-wide or on ``channel``.

    Lazily evicts each inspected key once it expires, so the live key set stays
    bounded. ponytail: a channel never re-checked keeps its expired key until the
    process restarts (in-memory, single-process); add a periodic sweep only if a
    long-lived listener watches very many channels.
    """
    cooled = False
    for key in {(account_id, None), (account_id, channel)}:
        until = _COOLDOWN_UNTIL.get(key)
        if until is None:
            continue
        if until > now:
            cooled = True
        else:
            del _COOLDOWN_UNTIL[key]
    return cooled


def clear_cooldown(account_id: str, channel: str | None = None) -> None:
    """Drop an account's account-wide and ``channel`` cooldowns (called on a successful post).

    In-memory only — no DB delete needed. A successful post is gated by
    ``in_cooldown``, so a cleared cooldown is necessarily already expired; its
    lapsed DB row is pruned by ``load_active_cooldowns`` on the next hydrate.
    """
    _COOLDOWN_UNTIL.pop((account_id, None), None)
    _COOLDOWN_UNTIL.pop((account_id, channel), None)


async def hydrate_cooldowns() -> None:
    """Reload persisted cooldown deadlines into the in-memory map after a restart (#34).

    Called once from the NC startup reconcile. Lapsed rows are pruned in the repo;
    each surviving deadline repopulates ``_COOLDOWN_UNTIL`` so a just-flooded account
    stays parked. The in-memory map remains the hot read path thereafter.
    """
    for record in await load_active_cooldowns(datetime.now(UTC).isoformat()):
        _COOLDOWN_UNTIL[(record.account_id, record.channel)] = datetime.fromisoformat(record.until)


def trip_channel_backoff(
    channel: str,
    now: datetime,
    *,
    base_seconds: float,
    max_seconds: float,
) -> float:
    """Escalate ``channel``'s deletion back-off and park it; returns the cooldown seconds.

    Each consecutive trip doubles the duration (``base * 2^prior_trips``), capped
    at ``max_seconds``. Trip count and cooldown are in-memory only, so a restart
    clears them (self-healing). The doubling loops rather than computing
    ``2**prior`` to stay overflow-proof if a channel keeps tripping for a long time.
    """
    prior = _CHANNEL_TRIPS.get(channel, 0)
    seconds = min(base_seconds, max_seconds)  # honour the cap even on the first trip
    for _ in range(prior):
        if seconds >= max_seconds:
            break
        seconds = min(seconds * 2, max_seconds)
    _CHANNEL_TRIPS[channel] = prior + 1
    _CHANNEL_COOLDOWN_UNTIL[channel] = now + timedelta(seconds=seconds)
    return seconds


def channel_in_backoff(channel: str, now: datetime) -> bool:
    """True while ``channel`` is parked by the deletion back-off (lazily evicts on expiry)."""
    until = _CHANNEL_COOLDOWN_UNTIL.get(channel)
    if until is None:
        return False
    if until > now:
        return True
    del _CHANNEL_COOLDOWN_UNTIL[channel]
    return False


@dataclass(frozen=True)
class ChannelDeletionScan:
    """One channel's deletion-sweep result, threaded into ``register_channel_deletions``."""

    window_ids: set[int]  # comment ids currently in the lookback window
    missing_ids: set[int]  # of those, the ones found vanished


def register_channel_deletions(  # noqa: PLR0913 - pure state fn; scan + trip knobs
    channel: str,
    now: datetime,
    scan: ChannelDeletionScan,
    *,
    min_deletions: int,
    base_seconds: float,
    max_seconds: float,
) -> float | None:
    """Count only *newly* vanished comments toward the back-off; trip once per episode.

    The vanished comments linger in the sweep's lookback window for hours, so a naive
    re-count each cooldown would walk one deletion episode to the cap. Ids already
    counted are remembered (pruned to the current window) and excluded, so escalation
    advances only when *genuinely new* comments vanish. Returns the cooldown seconds
    when this call trips the back-off (so the caller logs once), else ``None``.
    """
    counted = _CHANNEL_COUNTED_DELETED.get(channel, set()) & scan.window_ids  # prune aged-out ids
    new_missing = scan.missing_ids - counted
    _CHANNEL_COUNTED_DELETED[channel] = counted | scan.missing_ids
    if len(new_missing) < min_deletions:
        if not new_missing:
            _CHANNEL_TRIPS.pop(channel, None)  # clean window → let escalation decay
        return None
    return trip_channel_backoff(channel, now, base_seconds=base_seconds, max_seconds=max_seconds)


def register_challenge_failure(
    channel: str,
    now: datetime,
    *,
    min_failures: int,
    base_seconds: float,
    max_seconds: float,
) -> float | None:
    """Count a solver failure on ``channel``; trip an escalating back-off after K.

    Returns the cooldown seconds when *this* failure trips the back-off (so the
    caller logs the WARNING exactly once), else ``None``. The K counter resets on
    each trip and each consecutive trip doubles the duration (``base * 2^prior``,
    capped). In-memory only — a restart clears it (self-healing).
    """
    count = _CHALLENGE_FAILED.get(channel, 0) + 1
    if count < min_failures:
        _CHALLENGE_FAILED[channel] = count
        return None
    _CHALLENGE_FAILED[channel] = 0  # reset the window; escalation lives in _CHALLENGE_TRIPS
    prior = _CHALLENGE_TRIPS.get(channel, 0)
    seconds = min(base_seconds, max_seconds)  # honour the cap even on the first trip
    for _ in range(prior):
        if seconds >= max_seconds:
            break
        seconds = min(seconds * 2, max_seconds)
    _CHALLENGE_TRIPS[channel] = prior + 1
    _CHALLENGE_BACKOFF_UNTIL[channel] = now + timedelta(seconds=seconds)
    return seconds


def reset_challenge_failures(channel: str) -> None:
    """Zero ``channel``'s failure window on a solved challenge.

    ``register_challenge_failure`` counts *consecutive* failures, but only clears the
    counter when it trips. Without this, sporadic failures spread across many
    successes would accumulate to K and park a mostly-working channel — so a solved
    outcome resets the window. The escalation memory (``_CHALLENGE_TRIPS``) is left
    intact so a channel that keeps re-tripping still escalates.
    """
    _CHALLENGE_FAILED.pop(channel, None)


def is_channel_in_challenge_backoff(channel: str, now: datetime) -> bool:
    """True while ``channel`` is parked by the challenge back-off (lazily evicts on expiry)."""
    until = _CHALLENGE_BACKOFF_UNTIL.get(channel)
    if until is None:
        return False
    if until > now:
        return True
    del _CHALLENGE_BACKOFF_UNTIL[channel]
    return False


def reset_for_tests() -> None:
    """Test-only reset; production code never calls this."""
    _COOLDOWN_UNTIL.clear()
    _CHANNEL_TRIPS.clear()
    _CHANNEL_COOLDOWN_UNTIL.clear()
    _CHANNEL_COUNTED_DELETED.clear()
    _CHALLENGE_FAILED.clear()
    _CHALLENGE_TRIPS.clear()
    _CHALLENGE_BACKOFF_UNTIL.clear()
