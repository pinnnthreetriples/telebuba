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

The probe paths (``check_telegram_session`` / ``check_spam_status``) used to be
exempt, on the assumption that they run once per account lifecycle. They do
not — the operator can press the check button on any row at any time — and a
throwaway client on an account the pool already holds dies on the ``.session``
SQLite lock. They borrow from the pool like everyone else; only the login /
logout flows in ``_auth`` still build their own client, because they sign in on
or revoke that connection. Those flows also serialise against each other on
``_auth``'s own per-account lock — the tombstone below stops POOL rebuilds, not a
second ``_auth`` client.

Those flows are NOT exempt from the lock either — an account with a login in
flight is not "out of service": ``check_telegram_session`` pools a client for
every account it probes, new and unauthorized ones included, and ``_CLIENTS``
has no TTL, so the entry outlives the probe. Each of the three flows therefore
wraps its own client in :func:`removing_client`, which disconnects the pooled
twin and refuses rebuilds for the duration.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from core.logging import log_event
from core.telegram_client._client import create_telegram_client, prepare_telegram_client_profile
from schemas.device_fingerprint import TelegramClientRequest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from telethon import TelegramClient

    _RebuildHook = Callable[[str, TelegramClient], Awaitable[None]]

# Stdlib logging on purpose (mirrors ``core.proxy_check._failed_result``): a connect
# failure stringifies with the proxy endpoint — ``user:pass@host:port`` — and a session
# fault with the absolute ``.session`` path, so the full text must not ride ``extra``.
# ``log_event`` is not log-only: it persists ``extra`` as JSON in ``logs``, ``GET /logs``
# serves it back as ``LogEntry.extra`` and ``GET /events`` streams it. This lands on the
# uvicorn console and in loguru's ``debug.log`` via ``core.logging._StdlibToLoguru``, and
# on no route.
logger = logging.getLogger(__name__)

__all__ = [
    "TelegramClientPoolError",
    "TelegramClientUnavailableError",
    "evict_client",
    "get_client",
    "register_rebuild_hook",
    "removing_client",
    "shutdown_telegram_pool",
]


class TelegramClientPoolError(RuntimeError):
    """Raised when the pool fails to (re)connect a client.

    Transport failures (``OSError``) raise after Telethon's own connect ladder;
    other faults get one fresh attempt first.
    """

    def __init__(self, account_id: str, cause: Exception) -> None:
        super().__init__(f"telegram pool connect failed for {account_id}: {cause}")
        self.account_id = account_id
        self.cause = cause


class TelegramClientUnavailableError(TelegramClientPoolError):
    """The POOL refused to serve the account — shutdown, or a live tombstone.

    Distinct from its parent because nothing was learned about Telegram or the
    session here: no connect was attempted, and ``cause`` is the pool's own
    ``RuntimeError`` rather than a Telethon / transport failure. The probe paths
    have to tell the two apart — ``_session._probe_client`` unwraps ``cause`` so
    the real error reaches its classification ladder, and a bare ``RuntimeError``
    matched no arm there, so a login in flight wrote a sticky ``unknown_error``
    onto a row whose session was fine.

    A subclass, so every ``except TelegramClientPoolError`` borrower
    (``_actions``, ``_read``) keeps classifying it as an unavailable account.
    """


_CLIENTS: dict[str, TelegramClient] = {}
_CONNECT_LOCKS: dict[str, asyncio.Lock] = {}
_SHUTTING_DOWN = False

# Accounts whose removal is in flight — the per-account twin of ``_SHUTTING_DOWN``.
# See :func:`removing_client` for why a rebuild during that window is fatal.
# Refcounted rather than a set because two destructive paths can overlap on one
# account (a session-reset wipe racing a removal): an inner holder's exit must
# not lift the outer holder's tombstone.
_REMOVING: dict[str, int] = {}

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


def _is_removing(account_id: str) -> bool:
    """Is a removal in flight for ``account_id``? (see :func:`removing_client`).

    ``get_client`` asks this at three points on the way to a client and all
    three are load-bearing complements, not alternatives:

    * before the lock — a *cached* client must not be handed out under a live
      tombstone;
    * inside the lock — a borrower that queued on the lock before the mark
      appeared sits ahead of ``evict_client`` in FIFO order, so its pre-lock
      answer is already stale and it must still be refused;
    * after the build — a real connect takes seconds, so the mark can appear
      while this borrower holds the lock; publishing then hands out a client the
      removal is about to disconnect underneath its caller.

    Drop any one of them and a borrower re-opens (or re-creates) the ``.session``
    file the removal is about to unlink.
    """
    return account_id in _REMOVING


def _removing_error(account_id: str) -> TelegramClientUnavailableError:
    msg = "account is being removed"
    return TelegramClientUnavailableError(account_id, RuntimeError(msg))


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
    cached-but-disconnected we disconnect-and-rebuild once. A transport
    failure (``OSError``) or a second consecutive connect failure raises
    :class:`TelegramClientPoolError` so the caller's existing error path (see
    ``execute(...)``) classifies it like any other Telethon failure.
    """
    if _SHUTTING_DOWN:
        msg = "telegram pool is shutting down"
        raise TelegramClientUnavailableError(account_id, RuntimeError(msg))
    if _is_removing(account_id):
        raise _removing_error(account_id)

    cached = _CLIENTS.get(account_id)
    if cached is not None and cached.is_connected():
        return cached

    async with _connect_lock(account_id):
        if _is_removing(account_id):
            raise _removing_error(account_id)
        # Re-check under the lock — a peer may have connected while we waited.
        cached = _CLIENTS.get(account_id)
        if cached is not None and cached.is_connected():
            return cached
        if cached is not None:
            # Stale entry: drop the lost connection before rebuilding.
            await _safe_disconnect(cached)
            _CLIENTS.pop(account_id, None)

        client = await _connect_with_retry(account_id)

        # Covers both build attempts: connecting awaits, so a removal may have
        # marked the account while we held the lock. Throw the fresh client away
        # instead of publishing it — the removal's own unlink follows our lock
        # release and cleans up the session file this build just touched.
        if _is_removing(account_id):
            await _safe_disconnect(client)
            raise _removing_error(account_id)
        _CLIENTS[account_id] = client
    await _fire_rebuild_hooks(account_id, client)
    return client


async def _connect_with_retry(account_id: str) -> TelegramClient:
    """Build + connect under the caller's lock; one retry for non-transport faults."""
    try:
        return await _build_and_connect(account_id)
    except Exception as exc:  # second-attempt classifier sits below
        if isinstance(exc, OSError):
            # Transport failure: Telethon's connect() already ran its own retry ladder.
            raise await _connect_failed(account_id, exc) from exc
        # One retry for non-transport faults (busy ``.session`` handle, RuntimeError).
        logger.exception("pool connect failed for %s, retrying once", account_id)
        await log_event(
            "WARNING",
            "telegram_pool_connect_retry",
            account_id=account_id,
            extra={"first_error": type(exc).__name__},
        )
        try:
            return await _build_and_connect(account_id)
        except Exception as second_exc:
            raise await _connect_failed(account_id, second_exc) from second_exc


async def _connect_failed(account_id: str, exc: Exception) -> TelegramClientPoolError:
    """Log the final connect failure and build the error the borrower raises."""
    logger.exception("pool connect failed for %s", account_id)
    await log_event(
        "ERROR",
        "telegram_pool_connect_failed",
        account_id=account_id,
        extra={"error_type": type(exc).__name__},
    )
    return TelegramClientPoolError(account_id, exc)


async def _fire_rebuild_hooks(account_id: str, client: TelegramClient) -> None:
    """Let standing subscriptions re-register on a freshly built client.

    Runs outside the connect lock so a hook can safely re-enter the pool. A
    hook fault is logged and swallowed — a listener that can't re-attach must
    not break the borrower that triggered the rebuild.
    """
    for hook in _REBUILD_HOOKS:
        try:
            await hook(account_id, client)
        except Exception as exc:  # a hook fault must not break get_client.
            logger.exception("pool rebuild hook failed for %s", account_id)
            await log_event(
                "WARNING",
                "telegram_pool_rebuild_hook_failed",
                account_id=account_id,
                extra={"error_type": type(exc).__name__},
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


@asynccontextmanager
async def removing_client(account_id: str) -> AsyncIterator[None]:
    """Evict ``account_id``'s client and refuse rebuilds until the block exits.

    The eviction is skipped while the pool is shutting down (see
    :func:`evict_client`), which disconnects everything anyway; the tombstone is
    set regardless.

    Account removal evicts the client, unlinks the ``.session`` file, then
    deletes the DB row. ``evict_client`` on its own is not enough: every
    ``await`` in that sequence lets a concurrent borrower (post listener,
    warming loop, channel discovery) reach :func:`get_client`, which rebuilds a
    client and re-opens the session file — Telethon's ``SQLiteSession`` even
    re-creates it if it is already gone. On Windows the pending ``unlink`` then
    raises ``PermissionError`` and aborts the removal *before* the row is
    deleted; past the unlink it resurrects an orphan file for an account that no
    longer exists. Borrowers refused here raise
    :class:`TelegramClientUnavailableError`. ``execute(...)`` classifies that as an
    unavailable account — but only ``execute``: the two probe paths
    (``check_telegram_session``, ``check_spam_status``) run their own ladders and
    had to be taught the refusal separately, which is why it is a distinct class
    rather than a message on the parent.

    It refuses POOL rebuilds and nothing else. It does NOT serialise the ``_auth``
    flows against each other — each builds its own client and would open a second
    ``SQLiteSession`` on the same file; ``_auth``'s per-account lock covers that.
    Safe to nest inside that lock: this manager holds none across its ``yield``.
    """
    _REMOVING[account_id] = _REMOVING.get(account_id, 0) + 1
    try:
        # Marked before the eviction so a rebuild cannot slip into the gap.
        await evict_client(account_id)
        yield
    finally:
        holders = _REMOVING[account_id] - 1
        if holders:
            _REMOVING[account_id] = holders
        else:
            del _REMOVING[account_id]


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
    except Exception as exc:  # disconnect-on-error path
        # Don't crash shutdown on a half-dead client; just record it.
        logger.exception("pool disconnect failed")
        await log_event(
            "WARNING",
            "telegram_pool_disconnect_failed",
            extra={"error_type": type(exc).__name__},
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
    _REMOVING.clear()
    _SHUTTING_DOWN = False
