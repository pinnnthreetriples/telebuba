"""Counting and closing every open web.telegram.org window at once.

Each globe click keeps a full Chrome process per account, so the Accounts page needs
to know how many are up and to end them all in one action. Closing must leave
opening working (it is not shutdown) and must not forget any profile's login: a
window closed here reopens already signed in.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from services.accounts import web_login
from services.accounts.web_login import open_account_web
from services.accounts.web_windows import close_all_web_windows, count_open_web_windows
from tests.services.accounts._web_login_support import PROXY as _PROXY
from tests.services.accounts._web_login_support import SIGNED_IN_MARKER as _MARKER
from tests.services.accounts._web_login_support import FakeRelay as _FakeRelay
from tests.services.accounts._web_login_support import FakeWindow as _FakeWindow
from tests.services.accounts._web_login_support import counted_states as _counted_states
from tests.services.accounts._web_login_support import fresh_registry, relay
from tests.services.accounts._web_login_support import patch_profile as _patch_profile
from tests.services.accounts._web_login_support import patch_proxy as _patch_proxy
from tests.services.accounts._web_login_support import seeded as _seeded
from tests.services.accounts._web_login_support import wire as _wire

if TYPE_CHECKING:
    from pathlib import Path

# Re-bound so pytest collects the shared fixtures in this module's namespace.
__all__ = ["fresh_registry", "relay"]


def _register(account_id: str, *, running: bool = True) -> _FakeWindow:
    window = _FakeWindow()
    if not running:
        window.operator_closes()
    web_login._windows[account_id] = window  # ty: ignore[invalid-assignment]
    return window


@pytest.mark.asyncio
async def test_the_count_is_the_windows_still_on_screen() -> None:
    _register("a")
    _register("b")
    # Closed by hand in the browser: still registered until the next click drops it,
    # but no Chrome process is holding memory for it any more.
    _register("c", running=False)

    assert await count_open_web_windows() == 2


@pytest.mark.asyncio
async def test_a_window_whose_socket_dropped_still_counts_and_is_closed() -> None:
    # Fingerprint gone with the socket, but Chrome still runs and still holds memory;
    # the close watcher waits on the process, so nothing else would ever end it.
    window = _register("a")
    window.alive = False

    assert await count_open_web_windows() == 1
    assert await close_all_web_windows() == 1
    assert window.killed is True


@pytest.mark.asyncio
async def test_nothing_open_counts_and_closes_zero() -> None:
    assert await count_open_web_windows() == 0
    assert await close_all_web_windows() == 0


@pytest.mark.asyncio
async def test_close_all_kills_every_window_and_reports_the_live_ones() -> None:
    a = _register("a")
    b = _register("b")
    gone = _register("c", running=False)

    closed = await close_all_web_windows()

    assert closed == 2
    # KILLED, not detached: a leftover Chrome keeps the profile locked and its
    # --proxy-server pointed at a relay port another account may later bind.
    assert a.killed is True
    assert b.killed is True
    assert gone.killed is True
    assert web_login._windows == {}
    assert await count_open_web_windows() == 0


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_closed_window_reopens_already_signed_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, Any] = {}
    profile = _seeded(tmp_path / "acct")
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)
    window = _wire(monkeypatch, calls, states=["logged_in"])

    await open_account_web("acct")
    assert await close_all_web_windows() == 1
    window.alive = True  # the double hands the same window to the relaunch
    seen = _counted_states(monkeypatch, ["logged_in"])

    result = await open_account_web("acct")

    # Not shutdown: opening still works, and the login the profile already holds is
    # reused — no second QR token, so no second 'Active Sessions' device.
    assert (profile / _MARKER).exists()
    assert calls["launches"] == 2
    _port, _dir, capture = calls["launched"]
    assert capture is False
    assert "accepted" not in calls
    assert seen == ["logged_in"]
    assert result.signed_in is True


@pytest.mark.asyncio
async def test_close_all_releases_each_windows_relay(relay: type[_FakeRelay]) -> None:
    _register("a")
    fronting = relay(_PROXY)
    web_login._relays["a"] = (fronting, _PROXY)  # ty: ignore[invalid-assignment]

    await close_all_web_windows()

    # A relay left listening fronts a proxy nothing uses, for the life of the process.
    assert fronting.closed is True
    assert web_login._relays == {}


@pytest.mark.asyncio
async def test_close_all_waits_for_an_open_in_flight() -> None:
    window = _register("a")
    lock = await web_login._account_open_lock("a")

    async with lock:  # an open for this account is mid-login
        closing = asyncio.create_task(close_all_web_windows())
        done, _pending = await asyncio.wait({closing}, timeout=0.05)
        # Killing under a running open would pull the window out from under its drive.
        assert done == set()
        assert window.killed is False
    assert await closing == 1
    assert window.killed is True
