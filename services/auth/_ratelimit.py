"""In-memory login rate limiters (per-process): by client host, and by username (failures only).

ponytail: a single-worker process keeps all attempts in memory; move to a shared
store only if auth ever runs multi-process.
"""

from __future__ import annotations

from collections import defaultdict, deque

from core.config import settings

_attempts: dict[str, deque[float]] = defaultdict(deque)
_user_attempts: dict[str, deque[float]] = defaultdict(deque)

# Matches ``LoginRequest.username``'s max_length: longer input is cut so one request
# cannot park an arbitrarily large key in memory.
_USERNAME_KEY_MAX_LENGTH = 128
# Where usernames past ``login_name_rate_limit_max_tracked`` are counted together.
_OVERFLOW_KEY = "\0overflow"


def _evict_stale(attempts: dict[str, deque[float]], cutoff: float) -> None:
    """Drop buckets whose newest attempt is older than the window (memory leak fix).

    A one-shot client leaves a bucket behind that would otherwise linger forever;
    sweeping every idle key on each call keeps the dict bounded by the number of
    *active* clients, not the number of clients ever seen.
    """
    stale = [key for key, bucket in attempts.items() if not bucket or bucket[-1] < cutoff]
    for key in stale:
        del attempts[key]


def _record(
    attempts: dict[str, deque[float]],
    key: str,
    now: float,
    *,
    window: float,
    max_attempts: int,
) -> bool:
    cutoff = now - window
    _evict_stale(attempts, cutoff)
    bucket = attempts[key]
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= max_attempts:
        return False
    bucket.append(now)
    return True


def check_and_record(key: str, now: float) -> bool:
    """Return True if this attempt is within the per-client limit (and record it)."""
    return _record(
        _attempts,
        key,
        now,
        window=settings.auth.login_rate_limit_window_seconds,
        max_attempts=settings.auth.login_rate_limit_max_attempts,
    )


def _user_key(username: str) -> str:
    return username.strip().lower()[:_USERNAME_KEY_MAX_LENGTH]


def user_blocked(username: str, now: float) -> bool:
    """True when this name's failed attempts reached the limit inside the window.

    Only looks: nothing is recorded, so a successful login never counts. A name that is
    not tracked (never failed, or past the tracked-name cap) is never blocked.
    """
    window = settings.auth.login_name_rate_limit_window_seconds
    _evict_stale(_user_attempts, now - window)
    bucket = _user_attempts.get(_user_key(username))
    return bucket is not None and len(bucket) >= (settings.auth.login_name_rate_limit_max_attempts)


def check_user_and_record(username: str, now: float) -> bool:
    """Record one FAILED attempt for ``username``; False when it is over the limit.

    The name is stripped, lower-cased and length-capped. Once the tracked-name cap is
    reached a name that is not tracked is neither recorded nor blocked (the per-client
    limit still applies), so a flood of invented names cannot fill a shared bucket.
    """
    key = _user_key(username)
    window = settings.auth.login_name_rate_limit_window_seconds
    _evict_stale(_user_attempts, now - window)
    if key not in _user_attempts and len(_user_attempts) >= (
        settings.auth.login_name_rate_limit_max_tracked
    ):
        return True
    return _record(
        _user_attempts,
        key,
        now,
        window=window,
        max_attempts=settings.auth.login_name_rate_limit_max_attempts,
    )
