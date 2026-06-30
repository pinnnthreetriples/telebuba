"""In-memory TTL cache for pending phone-login challenges (single-worker).

Maps ``account_id`` → the ``phone_code_hash`` returned by the code request,
with an expiry. Single-worker is mandated (the split-stack ADR), so there is no
DB table — this mirrors ``services.neurocomment._state``. A submit *peeks* the
cached hash (so a wrong code can be retried without re-requesting); only a
successful sign-in or an explicit logout/reset forgets it.

Time is passed in (monotonic seconds) so the cache stays pure and testable.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PendingCode:
    """A requested login code's hash, valid until ``expires_at`` (monotonic s)."""

    phone: str
    phone_code_hash: str
    expires_at: float


_PENDING: dict[str, PendingCode] = {}


def remember_code(
    account_id: str,
    phone: str,
    phone_code_hash: str,
    *,
    now: float,
    ttl_seconds: float,
) -> None:
    """Store a freshly-requested code's hash, replacing any prior pending one."""
    _PENDING[account_id] = PendingCode(
        phone=phone,
        phone_code_hash=phone_code_hash,
        expires_at=now + ttl_seconds,
    )


def peek_code(account_id: str, *, now: float) -> PendingCode | None:
    """Return the pending code for an account, or ``None`` if missing/expired.

    Expired entries are evicted on read. Does not consume a valid entry — a
    wrong code can be re-submitted while the hash is still fresh.
    """
    pending = _PENDING.get(account_id)
    if pending is None:
        return None
    if pending.expires_at <= now:
        del _PENDING[account_id]
        return None
    return pending


def forget_code(account_id: str) -> None:
    """Drop any pending code for an account (after success, logout or reset)."""
    _PENDING.pop(account_id, None)
