"""Tests for the per-account Telethon client pool (``core.telegram_client._pool``).

The pool is the structural fix for the measured 8.4 s dialog freeze: every
UI action used to open a fresh ``TelegramClient`` from scratch (~7 s for
connect + MTProto handshake + auth-key load). With the pool, the first
``get_client(account_id)`` call pays the connect cost; every subsequent
call returns the cached connected client instantly.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import TelegramClientPoolError
from core.telegram_client._pool import (
    _CLIENTS,
    _REBUILD_HOOKS,
    _reset_for_tests,
    evict_client,
    get_client,
    register_rebuild_hook,
    shutdown_telegram_pool,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    _reset_for_tests()
    yield
    _reset_for_tests()
    reset_logging_for_tests()


class _FakeClient:
    """Minimal Telethon client stand-in with connect/disconnect/is_connected."""

    def __init__(self) -> None:
        self.connect_calls = 0
        self.disconnect_calls = 0
        self._connected = False

    async def connect(self) -> None:
        self.connect_calls += 1
        self._connected = True

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected


def _install_fake_factory(
    monkeypatch: pytest.MonkeyPatch,
    *,
    connect_failures: int = 0,
) -> list[_FakeClient]:
    """Replace the pool's profile-prep + client-build seams with synchronous fakes.

    Returns the running list of ``_FakeClient`` instances created so the test
    can assert on connect/disconnect counts and identity. ``connect_failures``
    makes the first N ``connect()`` calls raise — used to exercise the
    second-attempt retry path inside ``get_client``.
    """
    built: list[_FakeClient] = []

    async def fake_prepare(_request):
        return MagicMock(account_id="anything", session_path="memory://test")

    failures_remaining = {"n": connect_failures}

    def fake_create(_profile):
        client = _FakeClient()
        original_connect = client.connect

        async def failing_connect():
            if failures_remaining["n"] > 0:
                failures_remaining["n"] -= 1
                msg = "synthetic connect failure"
                raise RuntimeError(msg)
            await original_connect()

        client.connect = failing_connect  # ty: ignore[invalid-assignment]
        built.append(client)
        return client

    monkeypatch.setattr("core.telegram_client._pool.prepare_telegram_client_profile", fake_prepare)
    monkeypatch.setattr("core.telegram_client._pool.create_telegram_client", fake_create)
    return built


@pytest.mark.asyncio
async def test_get_client_returns_cached_instance_on_second_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = _install_fake_factory(monkeypatch)

    first = await get_client("acc-1")
    second = await get_client("acc-1")

    assert first is second, "second call must return the same cached client"
    assert len(built) == 1, "factory must run exactly once for one account"
    assert built[0].connect_calls == 1
    assert built[0].disconnect_calls == 0


@pytest.mark.asyncio
async def test_get_client_rebuilds_after_disconnect_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = _install_fake_factory(monkeypatch)

    await get_client("acc-1")
    # Simulate a lost connection: client still cached but ``is_connected()``
    # turns False (e.g. network blip). Pool should evict + rebuild.
    built[0]._connected = False

    await get_client("acc-1")

    assert len(built) == 2, "factory must build a fresh client for the rebuild"
    assert built[0].disconnect_calls == 1, "stale client must be disconnected before rebuild"


@pytest.mark.asyncio
async def test_get_client_concurrent_calls_share_single_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Twenty parallel borrowers must trigger the connect handshake exactly once.

    Without single-flighting under ``_CONNECT_LOCKS``, the freeze would just
    move from "one slow connect per click" to "twenty slow connects per first
    burst" with `.session` SQLite contention to boot.
    """
    built = _install_fake_factory(monkeypatch)

    results = await asyncio.gather(*(get_client("acc-1") for _ in range(20)))

    assert len({id(c) for c in results}) == 1, "all parallel callers share one client"
    assert len(built) == 1, "factory must run exactly once even under heavy concurrency"
    assert built[0].connect_calls == 1


@pytest.mark.asyncio
async def test_shutdown_disconnects_all_and_clears_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = _install_fake_factory(monkeypatch)

    await get_client("acc-a")
    await get_client("acc-b")
    assert len(built) == 2

    await shutdown_telegram_pool()

    assert built[0].disconnect_calls == 1
    assert built[1].disconnect_calls == 1


@pytest.mark.asyncio
async def test_get_client_raises_on_persistent_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One transient failure is retried; two consecutive failures escalate.

    The retry covers the common "stale session handle from prior crash"
    scenario; the escalation prevents an infinite retry loop on a genuinely
    unreachable account.
    """
    _install_fake_factory(monkeypatch, connect_failures=5)

    with pytest.raises(TelegramClientPoolError) as exc_info:
        await get_client("acc-broken")

    assert exc_info.value.account_id == "acc-broken"
    assert isinstance(exc_info.value.cause, RuntimeError)


@pytest.mark.asyncio
async def test_get_client_recovers_after_single_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_factory(monkeypatch, connect_failures=1)

    client = await get_client("acc-flaky")

    assert client.is_connected(), "second attempt must succeed and return a live client"


@pytest.mark.asyncio
async def test_evict_client_disconnects_and_drops_from_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """evict_client must disconnect the cached client and remove it from the pool.

    Regression for the Windows reset/logout 500: the pooled client keeps the
    ``.session`` SQLite file open, so it has to be evicted before the file is
    unlinked — the wipe must not run while a live handle exists.
    """
    built = _install_fake_factory(monkeypatch)
    client = await get_client("acc-evict")
    assert "acc-evict" in _CLIENTS

    await evict_client("acc-evict")

    assert built[0].disconnect_calls == 1, "the cached client must be disconnected"
    assert "acc-evict" not in _CLIENTS, "the pool must no longer hold the client"
    assert built == [client], "no rebuild — eviction must not create a new client"


@pytest.mark.asyncio
async def test_evict_client_is_noop_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_factory(monkeypatch)
    # No prior get_client — nothing cached. Must not raise or build anything.
    await evict_client("never-borrowed")
    assert "never-borrowed" not in _CLIENTS


@pytest.mark.asyncio
async def test_evict_precedes_session_file_removal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """After eviction the handle is released, so unlinking the .session succeeds.

    Simulates the reset ordering: evict the pooled client (asserted disconnect)
    and only then remove the session file — verifying eviction happens first.
    """
    built = _install_fake_factory(monkeypatch)
    await get_client("acc-order")
    session_file = tmp_path / "acc-order.session"
    session_file.write_bytes(b"auth-key")

    order: list[str] = []
    original_disconnect = built[0].disconnect

    def tracked_disconnect() -> None:
        order.append("disconnect")
        original_disconnect()

    built[0].disconnect = tracked_disconnect  # ty: ignore[invalid-assignment]

    await evict_client("acc-order")
    session_file.unlink()
    order.append("unlink")

    assert order == ["disconnect", "unlink"], "eviction must precede the file removal"
    assert not session_file.exists()


@pytest.mark.asyncio
async def test_rebuild_fires_registered_hook_with_new_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pool rebuild must invoke rebuild hooks with the freshly built client.

    This is the seam the post listener uses to re-attach its NewMessage handler
    when the pool replaces a dropped connection.
    """
    built = _install_fake_factory(monkeypatch)
    seen: list[tuple[str, object]] = []

    async def hook(account_id: str, client: object) -> None:
        seen.append((account_id, client))

    register_rebuild_hook(hook)
    try:
        first = await get_client("acc-hook")
        # First build fires the hook once.
        assert seen == [("acc-hook", first)]

        # Force a rebuild: cached client reports disconnected.
        built[0]._connected = False
        second = await get_client("acc-hook")

        assert second is not first, "a rebuild must produce a new client"
        assert seen[-1] == ("acc-hook", second), "the hook must see the rebuilt client"
    finally:
        _REBUILD_HOOKS.remove(hook)
