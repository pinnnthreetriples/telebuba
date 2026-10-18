"""Long-lived Telethon client pool — one connected client per account device.

Measured before this module existed: every UI action (dialog refresh, music
delete, etc.) opened a fresh ``TelegramClient`` via ``telegram_client(request)``,
paid ~7 s for connect + MTProto handshake + auth-key load, ran one request,
disconnected. Concurrent ownership by the warming runtime on the same
``.session`` SQLite file caused "seq_num too low" risk and lock contention.

This module keeps **exactly one** ``TelegramClient`` per ``(account_id, device)``
alive for the lifetime of the process. An account with a desktop AND a mobile
device holds two separate authorizations — two ``.session`` files, two
fingerprints — so its two clients live side by side and never evict each other,
and one ``.session`` is never opened by two clients. A borrower that names no
device gets the account's PRIMARY one (desktop when it has one): that is every
pre-device caller — listener, inbox, chats, reads, media, profile, neurocomment.
The first borrower pays the connect cost; every subsequent call reuses the open
socket. The warming runtime and the
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
from typing import TYPE_CHECKING, get_args

from core.db import fetch_account
from core.logging import log_event
from core.telegram_client._client import (
    DeviceUnavailableError,
    create_telegram_client,
    prepare_telegram_client_profile,
    resolve_device,
)
from core.telegram_client._pool_errors import (
    TelegramClientPoolError,
    TelegramClientUnavailableError,
    TelegramDeviceUnavailableError,
)
from core.telegram_client._pool_hooks import fire_rebuild_hooks, register_rebuild_hook
from schemas.device_fingerprint import DeviceType, TelegramClientRequest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from telethon import TelegramClient

_Key = tuple[str, DeviceType]
_DEVICE_TYPES: tuple[DeviceType, ...] = get_args(DeviceType)

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
    "TelegramDeviceUnavailableError",
    "evict_client",
    "forget_primary_device",
    "get_client",
    "register_rebuild_hook",
    "removing_client",
    "shutdown_telegram_pool",
]


_CLIENTS: dict[_Key, TelegramClient] = {}
_CONNECT_LOCKS: dict[_Key, asyncio.Lock] = {}
# The device a borrower naming none is served: cached because every action borrows,
# dropped whenever the account's devices can change (a device added, a removal).
_PRIMARY: dict[str, DeviceType] = {}
# Bumped by every forget, so a read that started before it cannot cache after it.
_PRIMARY_EPOCH: dict[str, int] = {}
_SHUTTING_DOWN = False

# Accounts whose removal is in flight — the per-account twin of ``_SHUTTING_DOWN``.
# See :func:`removing_client` for why a rebuild during that window is fatal.
# Refcounted rather than a set because two destructive paths can overlap on one
# account (a session-reset wipe racing a removal): an inner holder's exit must
# not lift the outer holder's tombstone.
_REMOVING: dict[str, int] = {}
# The same for ONE device: a sign-in rebuilding that device's session must not take
# the account's other device down with it (the second-device sign-in reads its code
# through the primary one).
_REMOVING_DEVICES: dict[_Key, int] = {}


def _is_removing(account_id: str, device_type: DeviceType | None = None) -> bool:
    """Is a removal in flight for ``account_id``? (see :func:`removing_client`).

    With ``device_type``: for the whole account or for that device. Without: for the
    whole account or any of its devices.

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
    if account_id in _REMOVING:
        return True
    devices = _DEVICE_TYPES if device_type is None else (device_type,)
    return any((account_id, device) in _REMOVING_DEVICES for device in devices)


def _removing_error(account_id: str) -> TelegramClientUnavailableError:
    msg = "account is being removed"
    return TelegramClientUnavailableError(account_id, RuntimeError(msg))


def _connect_lock(key: _Key) -> asyncio.Lock:
    lock = _CONNECT_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _CONNECT_LOCKS[key] = lock
    return lock


async def _primary_device(account_id: str) -> DeviceType:
    primary = _PRIMARY.get(account_id)
    if primary is None:
        epoch = _PRIMARY_EPOCH.get(account_id, 0)
        primary = resolve_device(await fetch_account(account_id), None)
        # A forget that landed while the row was being read means the answer may
        # predate the change: use it for this borrow, but do not keep it.
        if _PRIMARY_EPOCH.get(account_id, 0) == epoch:
            _PRIMARY[account_id] = primary
    return primary


def forget_primary_device(account_id: str) -> None:
    """Re-resolve the primary device on the next borrow (the account's devices changed).

    Clients already open stay open: adding a desktop to a mobile-only account makes
    desktop the primary for NEW borrows, while the mobile client keeps serving
    whoever asks for mobile by name.
    """
    _PRIMARY.pop(account_id, None)
    _PRIMARY_EPOCH[account_id] = _PRIMARY_EPOCH.get(account_id, 0) + 1


async def get_client(account_id: str, device_type: DeviceType | None = None) -> TelegramClient:
    """Return a connected Telethon client for one account device (cached or freshly built).

    ``device_type=None`` serves the account's primary device. A named device the
    account does not have raises :class:`TelegramDeviceUnavailableError` — the other
    device is never handed out in its place.

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
    device = device_type or await _primary_device(account_id)
    if _is_removing(account_id, device):
        raise _removing_error(account_id)
    key = (account_id, device)

    cached = _CLIENTS.get(key)
    if cached is not None and cached.is_connected():
        return cached

    async with _connect_lock(key):
        if _is_removing(account_id, device):
            raise _removing_error(account_id)
        # Re-check under the lock — a peer may have connected while we waited.
        cached = _CLIENTS.get(key)
        if cached is not None and cached.is_connected():
            return cached
        if cached is not None:
            # Stale entry: drop the lost connection before rebuilding.
            await _safe_disconnect(cached)
            _CLIENTS.pop(key, None)

        client = await _connect_with_retry(account_id, device)

        # Covers both build attempts: connecting awaits, so a removal may have
        # marked the account while we held the lock. Throw the fresh client away
        # instead of publishing it — the removal's own unlink follows our lock
        # release and cleans up the session file this build just touched.
        if _is_removing(account_id, device):
            await _safe_disconnect(client)
            raise _removing_error(account_id)
        _CLIENTS[key] = client
    # Standing subscriptions (post listener, inbox) live on the primary device only;
    # re-attaching them to the other device's client would deliver every update twice.
    if device == await _primary_device(account_id):
        await fire_rebuild_hooks(account_id, client)
    return client


async def _connect_with_retry(account_id: str, device: DeviceType) -> TelegramClient:
    """Build + connect under the caller's lock; one retry for non-transport faults."""
    try:
        return await _build_and_connect(account_id, device)
    except DeviceUnavailableError as exc:
        # Nothing was attempted against Telegram, so nothing to retry or to log as a
        # connect failure: the account has no such device to serve.
        raise TelegramDeviceUnavailableError(account_id, exc) from exc
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
            return await _build_and_connect(account_id, device)
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


async def evict_client(account_id: str, device_type: DeviceType | None = None) -> None:
    """Disconnect and drop the account's cached client(s); no-op if absent.

    ``device_type=None`` evicts every device of the account: a proxy change moves
    both authorizations, a removal deletes both.

    Callers that are about to touch the account's ``.session`` file on disk
    (logout/reset wipe, account removal) MUST evict first: on Windows the
    pooled client keeps the ``.session`` SQLite file open, so an ``unlink``
    would raise ``PermissionError`` while a handle is live. Safe when nothing
    is cached and during shutdown (which disconnects everything anyway).
    """
    if _SHUTTING_DOWN:
        return
    if device_type is None:
        forget_primary_device(account_id)
    for device in _DEVICE_TYPES if device_type is None else (device_type,):
        key = (account_id, device)
        async with _connect_lock(key):
            client = _CLIENTS.pop(key, None)
            if client is not None:
                await _safe_disconnect(client)


@asynccontextmanager
async def removing_client(
    account_id: str,
    device_type: DeviceType | None = None,
) -> AsyncIterator[None]:
    """Evict ``account_id``'s client(s) and refuse rebuilds until the block exits.

    ``device_type=None`` covers every device of the account (removal, rollback); a
    device scopes it to that one authorization (its login/logout, a second-device
    sign-in) and leaves the account's other device in service.

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
    marks: dict = _REMOVING if device_type is None else _REMOVING_DEVICES
    mark = account_id if device_type is None else (account_id, device_type)
    marks[mark] = marks.get(mark, 0) + 1
    try:
        # Marked before the eviction so a rebuild cannot slip into the gap.
        await evict_client(account_id, device_type)
        yield
    finally:
        holders = marks[mark] - 1
        if holders:
            marks[mark] = holders
        else:
            del marks[mark]
        if device_type is None:
            # Whatever a borrow cached while the account was being removed or rolled
            # back is about a row that may be gone; resolve it afresh next time.
            forget_primary_device(account_id)


async def _build_and_connect(account_id: str, device: DeviceType) -> TelegramClient:
    profile = await prepare_telegram_client_profile(
        TelegramClientRequest(account_id=account_id, device_type=device),
    )
    client = create_telegram_client(profile)
    try:
        await client.connect()
    except BaseException:
        # The constructor already opened the ``.session`` file, and a client that
        # failed (or was cancelled by a borrower's deadline) is never cached, so
        # nothing else can close it — on Windows the leaked handle made the
        # account's delete fail with ``PermissionError``.
        await _safe_disconnect(client)
        raise
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
    _PRIMARY.clear()
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
    _PRIMARY.clear()
    _PRIMARY_EPOCH.clear()
    _REMOVING.clear()
    _REMOVING_DEVICES.clear()
    _SHUTTING_DOWN = False
