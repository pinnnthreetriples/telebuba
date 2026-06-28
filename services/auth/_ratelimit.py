"""In-memory login rate limiter (per-process).

ponytail: a single-worker process keeps all attempts in memory; move to a shared
store only if auth ever runs multi-process.
"""

from __future__ import annotations

from collections import defaultdict, deque

from core.config import settings

_attempts: dict[str, deque[float]] = defaultdict(deque)


def check_and_record(key: str, now: float) -> bool:
    """Return True if this attempt is within the limit (and record it); False if over."""
    window = settings.auth.login_rate_limit_window_seconds
    bucket = _attempts[key]
    cutoff = now - window
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= settings.auth.login_rate_limit_max_attempts:
        return False
    bucket.append(now)
    return True
