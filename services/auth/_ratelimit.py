"""In-memory login rate limiter (per-process).

ponytail: a single-worker process keeps all attempts in memory; move to a shared
store only if auth ever runs multi-process.
"""

from __future__ import annotations

from collections import defaultdict, deque

from core.config import settings

_attempts: dict[str, deque[float]] = defaultdict(deque)


def _evict_stale(cutoff: float) -> None:
    """Drop buckets whose newest attempt is older than the window (memory leak fix).

    A one-shot client leaves a bucket behind that would otherwise linger forever;
    sweeping every idle key on each call keeps the dict bounded by the number of
    *active* clients, not the number of clients ever seen.
    """
    stale = [key for key, bucket in _attempts.items() if not bucket or bucket[-1] < cutoff]
    for key in stale:
        del _attempts[key]


def check_and_record(key: str, now: float) -> bool:
    """Return True if this attempt is within the limit (and record it); False if over."""
    window = settings.auth.login_rate_limit_window_seconds
    cutoff = now - window
    _evict_stale(cutoff)
    bucket = _attempts[key]
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= settings.auth.login_rate_limit_max_attempts:
        return False
    bucket.append(now)
    return True
