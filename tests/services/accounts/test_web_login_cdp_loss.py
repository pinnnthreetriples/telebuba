"""A browser that outlives its DevTools socket is ended: it would run without its fingerprint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from services.accounts import _web_watch, web_login
from services.accounts.web_login import open_account_web
from tests.services.accounts._web_login_support import PROXY as _PROXY
from tests.services.accounts._web_login_support import FakeRelay as _FakeRelay
from tests.services.accounts._web_login_support import fresh_registry, relay
from tests.services.accounts._web_login_support import patch_profile as _patch_profile
from tests.services.accounts._web_login_support import patch_proxy as _patch_proxy
from tests.services.accounts._web_login_support import wire as _wire

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["fresh_registry", "relay"]


@pytest.mark.asyncio
async def test_a_lost_cdp_socket_kills_the_browser_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    window = _wire(monkeypatch, calls, states=["logged_in"])

    await open_account_web("acct")
    (watcher,) = _web_watch._watchers
    assert window.running  # Chrome is alive...
    window.session.dropped.set()  # ...but its DevTools socket is gone
    await watcher

    assert window.killed is True  # the undressed browser is ended, not left to the operator
    assert web_login._windows == {}
    assert web_login._relays == {}
    assert relay.created[0].closed is True
