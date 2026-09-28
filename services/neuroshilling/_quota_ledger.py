"""Per-account quota lock and the ledger of autoreplies still in flight.

Scenario steps and autoreplies spend the same account quota. Both re-count under one
lock per account, and an autoreply claims its slot here before the model and
Telegram I/O, so a step counting in that window sees the reply it cannot yet read
from the chat log.
"""

from __future__ import annotations

import asyncio

# A plain dict needs no lock of its own: one uvicorn worker means one event loop, and
# ``asyncio.Lock`` binds to the running loop, so tests clear this between cases.
_ACCOUNT_LOCKS: dict[str, asyncio.Lock] = {}
_PENDING_REPLIES: set[tuple[str, str, str, int]] = set()


def account_lock(account_id: str) -> asyncio.Lock:
    """Shared quota lock for scenario steps and human replies on one account."""
    lock = _ACCOUNT_LOCKS.get(account_id)
    if lock is None:
        lock = _ACCOUNT_LOCKS[account_id] = asyncio.Lock()
    return lock


def reset_for_tests() -> None:
    _ACCOUNT_LOCKS.clear()
    _PENDING_REPLIES.clear()


def pending_reply_usage(campaign_id: str, account_id: str, target: str) -> tuple[int, int, int]:
    """In-flight autoreplies before their sent outcome reaches the chat log."""
    mine = [item for item in _PENDING_REPLIES if item[1] == account_id]
    return (
        len(mine),
        sum(item[2] == target for item in mine),
        sum(item[0] == campaign_id for item in mine),
    )


def hold_reply(campaign_id: str, account_id: str, target: str, message_id: int) -> None:
    _PENDING_REPLIES.add((campaign_id, account_id, target, message_id))


def release_reply(campaign_id: str, account_id: str, target: str, message_id: int) -> None:
    _PENDING_REPLIES.discard((campaign_id, account_id, target, message_id))
