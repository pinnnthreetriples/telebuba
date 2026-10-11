"""How long an account's relay and window live: exactly as long as the window does.

The relay is a loopback CONNECT proxy that exists only to front one window. Kept in the
registry after that window is gone, it is a listening socket nobody will use again, so
it goes with the window — whether we drop the window or the operator closes it — and the
next click starts a fresh one against the same signed-in profile. Same doubles as
``test_web_login``, from ``_web_login_support``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from services.accounts import _web_watch, web_login
from services.accounts.web_login import open_account_web
from tests.services.accounts._web_login_support import PROXY as _PROXY
from tests.services.accounts._web_login_support import FakeRelay as _FakeRelay
from tests.services.accounts._web_login_support import fresh_registry, relay
from tests.services.accounts._web_login_support import patch_profile as _patch_profile
from tests.services.accounts._web_login_support import patch_proxy as _patch_proxy
from tests.services.accounts._web_login_support import seeded as _seeded
from tests.services.accounts._web_login_support import wire as _wire

if TYPE_CHECKING:
    from pathlib import Path

# Re-bound so pytest collects the shared fixtures in this module's namespace.
__all__ = ["fresh_registry", "relay"]


def _only_watcher() -> asyncio.Task[None]:
    """The one window watcher running; await it to let its cleanup finish."""
    (watcher,) = _web_watch._watchers
    return watcher


@pytest.mark.asyncio
async def test_a_window_the_operator_closes_takes_its_relay_with_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    window = _wire(monkeypatch, calls, states=["logged_in"])

    await open_account_web("acct")
    watcher = _only_watcher()
    window.operator_closes()
    await watcher

    assert web_login._windows == {}
    assert web_login._relays == {}
    assert relay.created[0].closed is True
    # The socket is dropped too: nothing is left holding a dead browser.
    assert window.closed is True
    assert _web_watch._watchers == set()


@pytest.mark.asyncio
async def test_the_click_after_the_operator_closed_the_window_reopens_the_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    """A fresh relay, the same signed-in profile, and no second login driven."""
    calls: dict[str, Any] = {}
    profile = tmp_path / "acct"
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)
    window = _wire(monkeypatch, calls, states=["logged_in"])

    await open_account_web("acct")
    watcher = _only_watcher()
    window.operator_closes()
    await watcher
    result = await open_account_web("acct")

    assert result.signed_in is True
    assert calls["launches"] == 2
    fresh = relay.created[1]
    # Seeded by the first open, so relaunched WITHOUT the QR hook, through the new relay.
    assert calls["launched"] == (fresh.port, profile, False)
    assert web_login._relays["acct"][0] is fresh
    assert web_login._windows["acct"] is calls["windows"][1]


@pytest.mark.asyncio
async def test_a_window_we_drop_takes_its_relay_with_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    """A window found dead on the next click is dropped there, and so is its relay."""
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    window = _wire(monkeypatch, calls, states=["logged_in"])

    await open_account_web("acct")
    window.alive = False  # dead, and no watcher has noticed yet
    await open_account_web("acct")

    old, fresh = relay.created
    assert old.closed is True
    assert fresh.closed is False
    assert web_login._relays["acct"][0] is fresh


@pytest.mark.asyncio
async def test_a_window_replaced_mid_launch_relaunches_through_a_live_relay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    """A seeded profile whose web session died is relaunched within the same click.

    Dropping the hookless first window closes its relay, so the replacement must not
    be pointed at that relay's port.
    """
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, _seeded(tmp_path / "acct"))
    _wire(monkeypatch, calls, states=["qr", "qr", "logged_in"])

    await open_account_web("acct")

    assert calls["launches"] == 2
    live = relay.created[-1]
    assert web_login._relays["acct"][0] is live
    assert live.closed is False
    assert calls["launched"][0] == live.port


@pytest.mark.asyncio
async def test_a_stale_watcher_leaves_a_reopened_window_and_relay_alone(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    """The click lands first and relaunches; the old window's watcher runs after it."""
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    window = _wire(monkeypatch, calls, states=["logged_in"])

    await open_account_web("acct")
    watcher = _only_watcher()
    lock = await web_login._account_open_lock("acct")
    async with lock:
        reopen = asyncio.create_task(open_account_web("acct"))
        await asyncio.sleep(0)  # the click queues on the account lock first
        window.operator_closes()
        await asyncio.sleep(0)  # ...and the watcher queues behind it
    await reopen
    await watcher

    fresh_window = calls["windows"][1]
    assert web_login._windows["acct"] is fresh_window
    assert web_login._relays["acct"][0] is relay.created[1]
    assert relay.created[1].closed is False
    assert fresh_window.killed is False


@pytest.mark.asyncio
async def test_a_click_during_the_cleanup_waits_for_it_and_gets_a_fresh_relay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    """The cleanup owns the account until the old relay is closed.

    Otherwise a click landing after the window is deregistered but before its relay is
    closed reuses that relay's port for the new window — and the cleanup then closes
    the relay out from under it.
    """
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    window = _wire(monkeypatch, calls, states=["logged_in"])
    entered, gate = asyncio.Event(), asyncio.Event()
    real_kill = window.kill

    async def _slow_kill() -> None:
        entered.set()
        await gate.wait()
        await real_kill()

    monkeypatch.setattr(window, "kill", _slow_kill)

    await open_account_web("acct")
    watcher = _only_watcher()
    window.operator_closes()
    # The watcher is mid-cleanup, the window already deregistered.
    await asyncio.wait_for(entered.wait(), 1)
    reopen = asyncio.create_task(open_account_web("acct"))
    await asyncio.sleep(0)
    gate.set()
    await reopen
    await watcher

    old, fresh = relay.created
    assert old.closed is True
    assert fresh.closed is False
    assert calls["launched"][0] == fresh.port
    assert web_login._windows["acct"] is calls["windows"][1]


@pytest.mark.asyncio
async def test_shutdown_cancels_the_window_watchers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["logged_in"])

    await open_account_web("acct")
    watcher = _only_watcher()
    await web_login.shutdown_web_login()

    assert watcher.cancelled() is True
    assert _web_watch._watchers == set()
    assert relay.created[0].closed is True
