"""Transient per-account engine state — cooldowns after a flood/peer-flood.

# ponytail: single-process, in-memory only. This is NOT persisted: a process
# restart clears every cooldown (the DB claim table is the durable record, this
# is only short-lived anti-ban pacing). A multi-process deployment would need
# this in shared storage.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - runtime type for the public helpers

# account_id -> earliest UTC time the account may comment again.
_COOLDOWN_UNTIL: dict[str, datetime] = {}


def set_cooldown(account_id: str, until: datetime) -> None:
    """Park an account until ``until`` (extends an existing, later cooldown)."""
    current = _COOLDOWN_UNTIL.get(account_id)
    if current is None or until > current:
        _COOLDOWN_UNTIL[account_id] = until


def in_cooldown(account_id: str, now: datetime) -> bool:
    """True while the account is still inside its cooldown window."""
    until = _COOLDOWN_UNTIL.get(account_id)
    return until is not None and until > now


def clear_cooldown(account_id: str) -> None:
    """Drop an account's cooldown (called on a successful post)."""
    _COOLDOWN_UNTIL.pop(account_id, None)


def reset_for_tests() -> None:
    """Test-only reset; production code never calls this."""
    _COOLDOWN_UNTIL.clear()
