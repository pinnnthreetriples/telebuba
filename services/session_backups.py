"""Encrypted copies of every account's session files, made when they change and once a day.

Why: a ``.session`` file is the account, the only copy of its key. See ``core.session_vault``
for the copies themselves (encrypted with ``SESSION_VAULT__KEY``, versioned, an optional
mirror folder) and the quarantine removed files go to.

When a copy is made:

* at once, after anything that writes a key: an import (``account_added``), a sign-in
  (phone, QR, passkey), a re-key, a second device. A task reads the live log bus, like the
  operator bot, so none of those flows has to know about backups;
* in the session guard's pass (:func:`run_backup_pass`): each account at most once per
  ``SESSION_VAULT__BACKUP_INTERVAL_HOURS``, an unchanged file skipped by the vault itself.
  The same pass purges quarantined files past ``SESSION_VAULT__QUARANTINE_DAYS``.

Never raises: a backup that fails is logged (``session_backup_failed``, the error type
only) and the flow that triggered it goes on. With no key set nothing is copied, and the
first pass logs ``session_vault_off`` once per process so the operator knows.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account
from core.events import subscribe
from core.logging import log_event
from core.repositories.accounts import list_accounts
from core.session_vault import purge_quarantine, vault_enabled
from core.telegram_client import back_up_account_session

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.logs import LogEntry

__all__ = [
    "back_up_account",
    "reset_for_tests",
    "run_backup_pass",
    "shutdown_session_backups",
    "start_session_backups",
]

# The events after which an account's session files hold a new (or first) key.
_KEY_WRITTEN = frozenset(
    {
        "account_added",
        "phone_login_success",
        "qr_login_success",
        "passkey_login_success",
        "session_rekeyed",
        "account_device_added",
    }
)
_SECONDS_PER_HOUR = 3600


@dataclass
class _State:
    task: asyncio.Task[None] | None = None
    pending: set[asyncio.Task[None]] = field(default_factory=set)
    # When each account was last copied by this process (``time.monotonic``).
    copied: dict[str, float] = field(default_factory=dict)
    warned_off: bool = False


_state = _State()


async def back_up_account(account: AccountRead) -> int:
    """Copy every session file of ``account``; how many new versions were written."""
    written = 0
    # The primary file (its stored name, or the account id), then a second device's, if any.
    names = [account.session_name]
    if account.second_session_name is not None:
        names.append(account.second_session_name)
    for name in names:
        try:
            written += int(await back_up_account_session(account.account_id, name))
        except Exception as exc:  # noqa: BLE001 - a backup must never break its caller.
            await log_event(
                "WARNING",
                "session_backup_failed",
                account_id=account.account_id,
                extra={"error_type": type(exc).__name__},
            )
    _state.copied[account.account_id] = time.monotonic()
    return written


async def run_backup_pass() -> int:
    """Copy the accounts that are due and purge the old quarantine; how many versions."""
    with contextlib.suppress(OSError):
        await asyncio.to_thread(purge_quarantine)
    if not vault_enabled():
        if not _state.warned_off:
            _state.warned_off = True
            await log_event("WARNING", "session_vault_off")
        return 0
    every = settings.session_vault.backup_interval_hours * _SECONDS_PER_HOUR
    now = time.monotonic()
    written = 0
    for account in (await list_accounts()).accounts:
        last = _state.copied.get(account.account_id)
        if last is None or now - last >= every:
            written += await back_up_account(account)
    return written


async def _after_write(account_id: str) -> None:
    account = await fetch_account(account_id)
    if account is not None:
        await back_up_account(account)


def _schedule(entry: LogEntry) -> None:
    if entry.event not in _KEY_WRITTEN or entry.account_id is None or not vault_enabled():
        return
    task = asyncio.create_task(_after_write(entry.account_id))
    _state.pending.add(task)
    task.add_done_callback(_state.pending.discard)


async def _run(ready: asyncio.Event) -> None:
    async with subscribe() as queue:
        ready.set()
        while True:
            _schedule(await queue.get())


async def start_session_backups() -> None:
    """Subscribe to the log bus; returns once the subscription is live."""
    if _state.task is not None and not _state.task.done():
        return
    ready = asyncio.Event()
    _state.task = asyncio.create_task(_run(ready), name="session-backups")
    await ready.wait()


async def shutdown_session_backups() -> None:
    """Leave the bus; copies in flight finish (they are local file work, a moment each)."""
    task, _state.task = _state.task, None
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    if _state.pending:
        await asyncio.gather(*_state.pending, return_exceptions=True)


def reset_for_tests() -> None:
    global _state  # noqa: PLW0603 - one module-level runtime, reset between tests
    _state = _State()
