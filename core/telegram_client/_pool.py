"""Long-lived Telethon client pool — one connected client per account.

Measured before this module existed: every UI action (dialog refresh, music
delete, etc.) opened a fresh ``TelegramClient`` via ``telegram_client(request)``,
paid ~7 s for connect + MTProto handshake + auth-key load, ran one request,
disconnected. Concurrent ownership by the warming runtime on the same
``.session`` SQLite file caused "seq_num too low" risk and lock contention.

This module keeps **exactly one** ``TelegramClient`` per ``account_id`` alive
for the lifetime of the process. The first borrower pays the connect cost;
every subsequent call reuses the open socket. The warming runtime and the
profile-edit dialog both call into the same client through ``execute(...)``
/ ``execute_read_many(...)``.

Telethon's own ``MTProtoSender`` serialises requests on a single connection
and is concurrency-safe for parallel ``await client(...)`` calls — we do not
add a per-account request lock. The only lock is on connect/rebuild, to
single-flight the initial handshake when multiple callers race on the very
first ``get_client()`` for an account.

Probe paths (``check_telegram_session`` / ``check_spam_status``) deliberately
do NOT use the pool. They run once per account-lifecycle and benefit from a
clean throwaway session — see :func:`core.telegram_client._client.telegram_client`.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.logging import log_event
from core.telegram_client._client import create_telegram_client, prepare_telegram_client_profile
from schemas.device_fingerprint import TelegramClientRequest

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from telethon import TelegramClient

    _RebuildHook = Callable[[str, TelegramClient], Awaitable[None]]

__all__ = [
    "TelegramClientPoolError",
    "evict_client",
    "get_client",
    "register_rebuild_hook",
    "shutdown_telegram_pool",
]


class TelegramClientPoolError(RuntimeError):
    """Raised when the pool fails to (re)connect a client after one retry."""

    def __init__(self, account_id: str, cause: Exception) -> None:
        super().__init__(f"telegram pool connect failed for {account_id}: {cause}")
        self.account_id = account_id
        self.cause = cause


_CLIENTS: dict[str, TelegramClient] = {}
_CONNECT_LOCKS: dict[str, asyncio.Lock] = {}
_SHUTTING_DOWN = False

# Callbacks invoked after a fresh client is built for an account, so standing
# subscriptions (the post listener) can re-register their handlers on the new
# connection. ``_listener`` sets this at import time — the dependency points
# core→core, never into services.
_REBUILD_HOOKS: list[_RebuildHook] = []


def register_rebuild_hook(hook: _RebuildHook) -> None:
    """Register a callback fired with ``(account_id, client)`` after a rebuild.

    Idempotent per callable so a re-import can't stack duplicate hooks. Used by
    ``_listener`` to re-attach its ``NewMessage`` handler when the pool replaces
    a dropped connection.
    """
    if hook not in _REBUILD_HOOKS:
        _REBUILD_HOOKS.append(hook)


def _connect_lock(account_id: str) -> asyncio.Lock:
    lock = _CONNECT_LOCKS.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _CONNECT_LOCKS[account_id] = lock
    return lock


async def get_client(account_id: str) -> TelegramClient:
    """Return a connected Telethon client for ``account_id`` (cached or freshly built).

    Fast path: cached client whose ``is_connected()`` is True is returned
    directly without acquiring the per-account lock. Slow path: under the
    per-account lock we re-check, then build + connect a new client; on
    cached-but-disconnected we disconnect-and-rebuild once. A second
    consecutive connect failure raises :class:`TelegramClientPoolError`
    so the caller's existing error path (see ``execute(...)``) classifies
    it like any other Telethon failure.
    """
    if _SHUTTING_DOWN:
        msg = "telegram pool is shutting down"
        raise TelegramClientPoolError(account_id, RuntimeError(msg))

    cached = _CLIENTS.get(account_id)
    if cached is not None and cached.is_connected():
        return cached

    async with _connect_lock(account_id):
        # Re-check under the lock — a peer may have connected while we waited.
        cached = _CLIENTS.get(account_id)
        if cached is not None and cached.is_connected():
            return cached
        if cached is not None:
            # Stale entry: drop the lost connection before rebuilding.
            await _safe_disconnect(cached)
            _CLIENTS.pop(account_id, None)

        try:
            client = await _build_and_connect(account_id)
        except Exception as exc:  # noqa: BLE001 — second-attempt classifier sits below
            # One retry: fresh attempt, in case the first failed on a stale
            # session handle that build_and_connect's disconnect cleared up.
            await log_event(
                "WARNING",
                "telegram_pool_connect_retry",
                account_id=account_id,
                extra={"first_error": type(exc).__name__, "message": str(exc)},
            )
            try:
                client = await _build_and_connect(account_id)
            except Exception as second_exc:
                await log_event(
                    "ERROR",
                    "telegram_pool_connect_failed",
                    account_id=account_id,
                    extra={"error_type": type(second_exc).__name__, "message": str(second_exc)},
                )
                raise TelegramClientPoolError(account_id, second_exc) from second_exc

        _CLIENTS[account_id] = client
    await _fire_rebuild_hooks(account_id, client)
    return client


async def _fire_rebuild_hooks(account_id: str, client: TelegramClient) -> None:
    """Let standing subscriptions re-register on a freshly built client.

    Runs outside the connect lock so a hook can safely re-enter the pool. A
    hook fault is logged and swallowed — a listener that can't re-attach must
    not break the borrower that triggered the rebuild.
    """
    for hook in _REBUILD_HOOKS:
        try:
            await hook(account_id, client)
        except Exception as exc:  # noqa: BLE001 — a hook fault must not break get_client.
            await log_event(
                "WARNING",
                "telegram_pool_rebuild_hook_failed",
                account_id=account_id,
                extra={"error_type": type(exc).__name__, "message": str(exc)},
            )


async def evict_client(account_id: str) -> None:
    """Disconnect and drop the cached client for ``account_id``; no-op if absent.

    Callers that are about to touch the account's ``.session`` file on disk
    (logout/reset wipe, account removal) MUST evict first: on Windows the
    pooled client keeps the ``.session`` SQLite file open, so an ``unlink``
    would raise ``PermissionError`` while a handle is live. Safe when nothing
    is cached and during shutdown (which disconnects everything anyway).
    """
    if _SHUTTING_DOWN:
        return
    async with _connect_lock(account_id):
        client = _CLIENTS.pop(account_id, None)
        if client is not None:
            await _safe_disconnect(client)


async def _build_and_connect(account_id: str) -> TelegramClient:
    profile = await prepare_telegram_client_profile(
        TelegramClientRequest(account_id=account_id),
    )
    client = create_telegram_client(profile)
    await client.connect()
    return client


async def _safe_disconnect(client: TelegramClient) -> None:
    """Disconnect ignoring already-disconnected and shutdown-race errors.

    ``client.disconnect()`` returns either ``None`` or an awaitable depending
    on Telethon's internal state — the wrapper normalises both shapes so
    callers can ``await`` unconditionally.
    """
    try:
        result = client.disconnect()
        if asyncio.iscoroutine(result):
            await result
    except Exception as exc:  # noqa: BLE001 — disconnect-on-error path
        # Don't crash shutdown on a half-dead client; just record it.
        await log_event(
            "WARNING",
            "telegram_pool_disconnect_failed",
            extra={"error_type": type(exc).__name__, "message": str(exc)},
        )


async def shutdown_telegram_pool() -> None:
    """Disconnect every pooled client and clear caches.

    Registered as the LAST ``app.on_shutdown`` handler in :mod:`main` — after
    ``shutdown_warming_runtime`` drained its in-flight ``execute(...)``
    calls. Telethon's ``disconnect()`` flushes the ``.session`` SQLite
    synchronously, so once we return the on-disk state is consistent.
    """
    global _SHUTTING_DOWN  # noqa: PLW0603 — module-level flag is the simplest signal here
    _SHUTTING_DOWN = True
    clients = list(_CLIENTS.values())
    _CLIENTS.clear()
    _CONNECT_LOCKS.clear()
    if clients:
        await asyncio.gather(
            *(_safe_disconnect(client) for client in clients),
            return_exceptions=True,
        )
    _SHUTTING_DOWN = False


def _reset_for_tests() -> None:
    """Test-only reset; production code never calls this."""
    global _SHUTTING_DOWN  # noqa: PLW0603
    _CLIENTS.clear()
    _CONNECT_LOCKS.clear()
    _SHUTTING_DOWN = False
