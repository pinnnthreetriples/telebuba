"""Transient per-account engine state — cooldowns after a flood/peer-flood.

# ponytail: single-process, in-memory only. This is NOT persisted: a process
# restart clears every cooldown (the DB claim table is the durable record, this
# is only short-lived anti-ban pacing). A multi-process deployment would need
# this in shared storage.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - runtime type for the public helpers

# (account_id, channel) -> earliest UTC time it may comment again. channel=None is
# an account-wide cooldown (flood/peer-flood); a channel scopes it to that chat
# (slow-mode is per-chat, so it must not park the account everywhere).
_COOLDOWN_UNTIL: dict[tuple[str, str | None], datetime] = {}


def set_cooldown(account_id: str, until: datetime, channel: str | None = None) -> None:
    """Park ``(account, channel)`` until ``until`` (extends an existing, later cooldown)."""
    key = (account_id, channel)
    current = _COOLDOWN_UNTIL.get(key)
    if current is None or until > current:
        _COOLDOWN_UNTIL[key] = until


def in_cooldown(account_id: str, now: datetime, channel: str | None = None) -> bool:
    """True while the account is cooled account-wide or on ``channel``."""
    for key in ((account_id, None), (account_id, channel)):
        until = _COOLDOWN_UNTIL.get(key)
        if until is not None and until > now:
            return True
    return False


def clear_cooldown(account_id: str, channel: str | None = None) -> None:
    """Drop an account's account-wide and ``channel`` cooldowns (called on a successful post)."""
    _COOLDOWN_UNTIL.pop((account_id, None), None)
    _COOLDOWN_UNTIL.pop((account_id, channel), None)


def reset_for_tests() -> None:
    """Test-only reset; production code never calls this."""
    _COOLDOWN_UNTIL.clear()
