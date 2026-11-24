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
only) and the flow that triggered it goes on. An account counts as copied only when every
one of its files was handled without an exception; after a failure it is tried again after
``SESSION_VAULT__RETRY_MINUTES`` (not on every guard pass, not after a whole interval), also
when the failed copy came from an import or a re-key. With no key set nothing is copied, and
the first pass logs ``session_vault_off`` once per process so the operator knows.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account
from core.events import subscribe
from core.logging import log_event
from core.passkey_vault import PasskeyVaultError
from core.passkey_vault_snapshot import (
    newest_vault_snapshot_at,
    snapshot_enabled,
    take_vault_snapshot,
)
from core.repositories.accounts import list_accounts
from core.session_vault import copy_meta, purge_quarantine, vault_enabled
from core.telegram_client import back_up_account_session
from services.accounts._recovery_bundle import build_recovery_bundle

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
_SECONDS_PER_MINUTE = 60


@dataclass
class _State:
    task: asyncio.Task[None] | None = None
    pending: set[asyncio.Task[None]] = field(default_factory=set)
    # When each account was last copied completely by this process (``time.monotonic``).
    copied: dict[str, float] = field(default_factory=dict)
    # When each account's last copy attempt failed; it is retried after the retry delay.
    failed: dict[str, float] = field(default_factory=dict)
    warned_off: bool = False
    # When the last key-vault snapshot failed (``time.monotonic``); retried after the retry delay.
    vault_failed: float | None = None


_state = _State()


async def _bundle_or_none(account: AccountRead) -> bytes | None:
    """The recovery bundle for the copies; one that cannot be built never fails the copy."""
    try:
        return await build_recovery_bundle(account)
    except Exception as exc:  # noqa: BLE001 - the key copy matters more than its bundle.
        await log_event(
            "WARNING",
            "session_bundle_failed",
            account_id=account.account_id,
            extra={"error_type": type(exc).__name__},
        )
        return None


async def back_up_account(account: AccountRead) -> int:
    """Copy every session file of ``account``; how many new versions were written.

    The account counts as copied only when no file raised; otherwise it is marked failed.
    """
    written = 0
    complete = True
    token = copy_meta.set(await _bundle_or_none(account))
    # The primary file (its stored name, or the account id), then a second device's, if any.
    names = [account.session_name]
    if account.second_session_name is not None:
        names.append(account.second_session_name)
    try:
        for name in names:
            try:
                written += int(await back_up_account_session(account.account_id, name))
            except Exception as exc:  # noqa: BLE001 - a backup must never break its caller.
                complete = False
                await log_event(
                    "WARNING",
                    "session_backup_failed",
                    account_id=account.account_id,
                    extra={"error_type": type(exc).__name__},
                )
    finally:
        # Also on a cancellation: the next account must never be copied with this bundle.
        copy_meta.reset(token)
    if complete:
        _state.copied[account.account_id] = time.monotonic()
        _state.failed.pop(account.account_id, None)
    else:
        _state.copied.pop(account.account_id, None)
        _state.failed[account.account_id] = time.monotonic()
    return written


def _due(account_id: str, now: float) -> bool:
    """Whether the pass copies ``account_id`` now: its interval ran out, or its retry delay did."""
    last = _state.copied.get(account_id)
    if last is not None:
        return now - last >= settings.session_vault.backup_interval_hours * _SECONDS_PER_HOUR
    failed_at = _state.failed.get(account_id)
    if failed_at is None:
        return True
    return now - failed_at >= settings.session_vault.retry_minutes * _SECONDS_PER_MINUTE


async def _snapshot_key_vault() -> None:
    """Once per DB-backup interval, when ``PASSKEY_VAULT__SNAPSHOT_TOKEN`` is set; never raises."""
    if not snapshot_enabled():
        return
    failed_at = _state.vault_failed
    if failed_at is not None and (
        time.monotonic() - failed_at < settings.session_vault.retry_minutes * _SECONDS_PER_MINUTE
    ):
        return
    _state.vault_failed = None
    try:
        newest = await asyncio.to_thread(newest_vault_snapshot_at)
        interval = settings.db.backup_interval_hours * _SECONDS_PER_HOUR
        if newest is not None and (datetime.now(UTC) - newest).total_seconds() < interval:
            return
        await take_vault_snapshot()
    except PasskeyVaultError as exc:
        _state.vault_failed = time.monotonic()
        await log_event(
            "WARNING",
            "passkey_vault_snapshot_failed",
            extra={"error_type": exc.code, "status": exc.detail},
        )
    except Exception as exc:  # noqa: BLE001 - a vault snapshot must never break the pass.
        _state.vault_failed = time.monotonic()
        await log_event(
            "WARNING",
            "passkey_vault_snapshot_failed",
            extra={"error_type": type(exc).__name__},
        )


async def run_backup_pass() -> int:
    """Copy the accounts that are due and purge the old quarantine; how many versions."""
    with contextlib.suppress(OSError):
        await asyncio.to_thread(purge_quarantine)
    await _snapshot_key_vault()
    if not vault_enabled():
        if not _state.warned_off:
            _state.warned_off = True
            await log_event("WARNING", "session_vault_off")
        return 0
    now = time.monotonic()
    written = 0
    for account in (await list_accounts()).accounts:
        if _due(account.account_id, now):
            written += await back_up_account(account)
    return written


async def _after_write(account_id: str) -> None:
    try:
        account = await fetch_account(account_id)
        if account is not None:
            await back_up_account(account)
    except Exception as exc:  # noqa: BLE001 - a failed copy must not leave an unretrieved task error.
        _state.copied.pop(account_id, None)
        _state.failed[account_id] = time.monotonic()
        await log_event(
            "WARNING",
            "session_backup_failed",
            account_id=account_id,
            extra={"error_type": type(exc).__name__},
        )


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
            entry = await queue.get()
            try:
                _schedule(entry)
            except Exception as exc:  # noqa: BLE001 - the subscriber must outlive any one event.
                await log_event(
                    "WARNING", "session_backup_failed", extra={"error_type": type(exc).__name__}
                )


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
