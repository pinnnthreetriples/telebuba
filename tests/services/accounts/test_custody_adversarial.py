"""Adversarial probes of the PR-6 custody slice (web windows, tombstones, phone sign-in)."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

import pytest

from core.emulator import EmulatorError
from core.web_login import _lifetime
from core.web_login._tombstone import TOMBSTONE_NAME, list_marked_profiles
from services.accounts import _phone_authorization, web_login, web_windows
from services.accounts._web_errors import WebLoginLaunchError
from services.accounts.web_login import open_account_web
from services.accounts.web_windows import close_account_web_window, forget_account_web
from tests.services.accounts._web_login_support import PROXY as _PROXY
from tests.services.accounts._web_login_support import fresh_registry, relay
from tests.services.accounts._web_login_support import patch_profile as _patch_profile
from tests.services.accounts._web_login_support import patch_proxy as _patch_proxy
from tests.services.accounts._web_login_support import wire as _wire

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["fresh_registry", "relay"]


@pytest.mark.asyncio
async def test_a_failed_removal_still_leaves_the_tombstone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "web_profiles"
    profile = root / "acct"
    (profile / "Default").mkdir(parents=True)
    (profile / "lockfile").write_text("held", encoding="utf-8")
    monkeypatch.setattr("core.web_login._tombstone.web_profiles_root", lambda: root)
    monkeypatch.setattr(web_windows, "account_profile_dir", lambda _id: profile)
    monkeypatch.setattr(_lifetime, "_REMOVE_TRIES", 1)
    monkeypatch.setattr(_lifetime, "_REMOVE_RETRY_DELAY", 0)
    real_unlink = os.unlink

    def _locked(path: Any, *a: Any, **k: Any) -> None:
        if str(path).endswith("lockfile"):
            raise PermissionError(13, "locked by a Chrome helper")
        real_unlink(path, *a, **k)

    monkeypatch.setattr(os, "unlink", _locked)

    await forget_account_web("acct")

    assert (profile / "lockfile").exists()  # the removal really failed
    assert [p.name for p in await list_marked_profiles()] == ["acct"]
    assert (profile / TOMBSTONE_NAME).exists()


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_window_launched_by_an_open_in_flight_does_not_survive_a_proxy_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["logged_in"])
    monkeypatch.setattr(web_windows, "_OPEN_LOCK_WAIT_SECONDS", 0.05)
    inner = web_login.launch_account_web
    entered, release = asyncio.Event(), asyncio.Event()

    async def _gated(*a: Any, **k: Any) -> Any:
        entered.set()
        await release.wait()
        return await inner(*a, **k)

    monkeypatch.setattr(web_login, "launch_account_web", _gated)
    opening = asyncio.create_task(open_account_web("acct"))
    await asyncio.wait_for(entered.wait(), 2)

    assert await close_account_web_window("acct") is False  # the proxy changed: nothing yet
    release.set()
    # The open that was overtaken ends its own window and says it failed: the operator retries.
    with pytest.raises(WebLoginLaunchError):
        await asyncio.wait_for(opening, 5)

    assert "acct" not in web_login._windows  # nothing may stay up on the OLD proxy


@pytest.mark.asyncio
async def test_background_refuses_the_app_again_on_the_next_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {"h1": "phone"}

    async def _fetch(_account_id: str) -> dict[str, str]:
        return dict(rows)

    async def _forget(_account_id: str, hash_: str) -> None:
        rows.pop(hash_, None)

    async def _alive(_account_id: str) -> frozenset[str]:
        return frozenset({"h1"})

    async def _log(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(_phone_authorization, "fetch_own_authorizations", _fetch)
    monkeypatch.setattr(_phone_authorization, "forget_own_authorization", _forget)
    monkeypatch.setattr(_phone_authorization, "_listed_hashes", _alive)
    monkeypatch.setattr(_phone_authorization, "log_event", _log)

    with pytest.raises(EmulatorError):
        await _phone_authorization.settle_recorded_authorization("a", background=True)
    with pytest.raises(EmulatorError):  # the app is still signed out; still no operator
        await _phone_authorization.settle_recorded_authorization("a", background=True)
