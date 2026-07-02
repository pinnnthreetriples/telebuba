"""Transient per-account engine state — cooldowns after a flood/peer-flood.

# ponytail: single-process, in-memory only. This is NOT persisted: a process
# restart clears every cooldown (the DB claim table is the durable record, this
# is only short-lived anti-ban pacing). A multi-process deployment would need
# this in shared storage.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# (account_id, channel) -> earliest UTC time it may comment again. channel=None is
# an account-wide cooldown (flood/peer-flood); a channel scopes it to that chat
# (slow-mode is per-chat, so it must not park the account everywhere).
_COOLDOWN_UNTIL: dict[tuple[str, str | None], datetime] = {}

# Deletion back-off (#131), keyed by channel — the periodic sweep trips it when too
# many of a channel's comments vanish. Mirrors the per-account cooldown above but
# channel-scoped, in-memory only (recomputed each sweep, self-healing on restart).
_CHANNEL_TRIPS: dict[str, int] = {}  # consecutive trips this process (escalation memory)
_CHANNEL_COOLDOWN_UNTIL: dict[str, datetime] = {}  # earliest UTC time to comment again

# Challenge back-off (#147), keyed by channel — K consecutive solver failures
# (pending → failed) trip an escalating cooldown that stops onboarding new accounts
# into the channel. Mirrors the deletion back-off above; in-memory, self-healing.
_CHALLENGE_FAILED: dict[str, int] = {}  # failures since the last trip (the K counter)
_CHALLENGE_TRIPS: dict[str, int] = {}  # consecutive trips this process (escalation memory)
_CHALLENGE_BACKOFF_UNTIL: dict[str, datetime] = {}  # earliest UTC time to onboard again


def set_cooldown(account_id: str, until: datetime, channel: str | None = None) -> None:
    """Park ``(account, channel)`` until ``until`` (extends an existing, later cooldown)."""
    key = (account_id, channel)
    current = _COOLDOWN_UNTIL.get(key)
    if current is None or until > current:
        _COOLDOWN_UNTIL[key] = until


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
    """Drop an account's account-wide and ``channel`` cooldowns (called on a successful post)."""
    _COOLDOWN_UNTIL.pop((account_id, None), None)
    _COOLDOWN_UNTIL.pop((account_id, channel), None)


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
    _CHALLENGE_FAILED.clear()
    _CHALLENGE_TRIPS.clear()
    _CHALLENGE_BACKOFF_UNTIL.clear()
