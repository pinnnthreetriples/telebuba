"""A web window never outlives its proxy, and a deleted account's profile is never left behind."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from core.web_login import _lifetime
from core.web_login._tombstone import TOMBSTONE_NAME, clear_removal_mark
from schemas.proxy import ProxyCreateAssignment
from services import proxies
from services.accounts import web_login, web_windows
from services.accounts.web_windows import (
    close_account_web_window,
    forget_account_web,
    remove_orphaned_web_profiles,
)
from tests.services.accounts._web_login_support import PROXY as _PROXY
from tests.services.accounts._web_login_support import FakeRelay as _FakeRelay
from tests.services.accounts._web_login_support import FakeWindow as _FakeWindow
from tests.services.accounts._web_login_support import fresh_registry

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["fresh_registry"]


def _open_window(account_id: str) -> tuple[_FakeWindow, _FakeRelay]:
    window = _FakeWindow()
    relay = _FakeRelay(_PROXY)
    web_login._windows[account_id] = window  # ty: ignore[invalid-assignment]
    web_login._relays[account_id] = (relay, _PROXY)  # ty: ignore[invalid-assignment]
    return window, relay


async def _quiet_log(*_a: Any, **_k: Any) -> None:
    return None


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, Any]]:
    events: list[tuple[str, str, Any]] = []

    async def _log(level: str, event: str, **kw: Any) -> None:
        events.append((level, event, kw.get("extra")))

    monkeypatch.setattr(web_windows, "log_event", _log)
    return events


# ---------------------------------------------------------------- proxy change


@pytest.mark.asyncio
async def test_closing_ends_the_window_and_relay_and_logs_the_reason(
    logged: list[tuple[str, str, Any]],
) -> None:
    window, relay = _open_window("a")

    assert await close_account_web_window("a") is True

    assert window.killed is True
    assert relay.closed is True
    assert "a" not in web_login._windows
    assert ("INFO", "account_web_window_closed", {"reason": "proxy_changed"}) in logged


@pytest.mark.asyncio
async def test_closing_with_no_window_logs_nothing(logged: list[tuple[str, str, Any]]) -> None:
    assert await close_account_web_window("a") is False
    assert logged == []


@pytest.mark.asyncio
async def test_a_close_failure_never_raises(
    monkeypatch: pytest.MonkeyPatch, logged: list[tuple[str, str, Any]]
) -> None:
    _open_window("a")

    async def _boom(*_a: object) -> None:
        raise RuntimeError

    monkeypatch.setattr(web_login, "_drop_window", _boom)

    assert await close_account_web_window("a") is False
    assert [e[1] for e in logged] == ["account_web_window_close_failed"]


@pytest.mark.asyncio
async def test_a_login_in_progress_does_not_hold_the_close_beyond_the_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_windows, "_OPEN_LOCK_WAIT_SECONDS", 0.05)
    window, _relay = _open_window("a")
    lock = await web_login._account_open_lock("a")
    async with lock:  # a drive holding the open lock
        closed = await asyncio.wait_for(close_account_web_window("a"), timeout=2)
        assert lock.locked()  # still the drive's, not released by the close

    assert closed is True
    assert window.killed is True


def _spy_close(monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> list[str]:
    closed: list[str] = []

    async def _close(account_id: str) -> bool:
        closed.append(account_id)
        return not fail

    async def _noop(*_a: object) -> None:
        return None

    monkeypatch.setattr(proxies, "close_account_web_window", _close)
    monkeypatch.setattr(proxies, "evict_client", _noop)
    monkeypatch.setattr(proxies, "stop_account_pod", _noop)
    monkeypatch.setattr(proxies, "log_event", _quiet_log)
    return closed


@pytest.mark.asyncio
async def test_each_proxy_function_closes_exactly_the_affected_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = _spy_close(monkeypatch)

    async def _assign(_proxy_id: str, _account_id: str) -> object:
        return object()

    async def _create(_data: object) -> object:
        return type("P", (), {"id": "p"})()

    async def _unassign(*_a: object) -> None:
        return None

    async def _on_proxy(_proxy_id: str) -> list[str]:
        return ["x", "y"]

    async def _standalone(_proxy_id: str) -> list[str]:
        return []

    monkeypatch.setattr(proxies, "assign_account_to_proxy", _assign)
    monkeypatch.setattr(proxies, "create_and_assign_proxy", _create)
    monkeypatch.setattr(proxies, "unassign_account_from_proxy", _unassign)
    monkeypatch.setattr(proxies, "list_account_ids_for_proxy", _on_proxy)
    monkeypatch.setattr(proxies, "standalone_ids_on_proxy", _standalone)
    monkeypatch.setattr(proxies, "stop_standalone_pods", _unassign)
    monkeypatch.setattr(proxies, "delete_proxy", _unassign)

    await proxies.assign_proxy("p", "one")
    await proxies.assign_proxy_by_endpoint(
        ProxyCreateAssignment.model_construct(
            account_id="two", proxy_type="socks5", host="h", port=1
        )
    )
    await proxies.unassign_proxy("three")
    await proxies.remove_proxy("p")

    assert closed == ["one", "two", "three", "x", "y"]


@pytest.mark.asyncio
async def test_a_proxy_change_survives_a_close_that_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real close never raises; a False answer must not stop the change either."""
    closed = _spy_close(monkeypatch, fail=True)

    async def _unassign(*_a: object) -> None:
        return None

    monkeypatch.setattr(proxies, "unassign_account_from_proxy", _unassign)

    await proxies.unassign_proxy("three")

    assert closed == ["three"]


# ------------------------------------------------------------ orphan profiles


@pytest.fixture
def root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "web_profiles"
    path.mkdir()
    monkeypatch.setattr("core.web_login._tombstone.web_profiles_root", lambda: path)
    monkeypatch.setattr(web_windows, "account_profile_dir", lambda account_id: path / account_id)
    return path


def _profile(root: Path, name: str, *, marked: bool) -> Path:
    path = root / name
    (path / "Default").mkdir(parents=True)
    (path / "Default" / "Cookies").write_text("session", encoding="utf-8")
    if marked:
        (path / TOMBSTONE_NAME).write_text("x", encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_the_sweep_removes_only_marked_profiles(
    root: Path, logged: list[tuple[str, str, Any]]
) -> None:
    gone = _profile(root, "deleted", marked=True)
    kept = _profile(root, "unmarked-no-account-in-db", marked=False)
    (root / "stray-file").write_text("x", encoding="utf-8")

    assert await remove_orphaned_web_profiles() == 1

    assert not gone.exists()
    assert kept.exists()
    assert root.exists()
    assert ("INFO", "account_web_profile_orphan_removed", {"count": 1}) in logged


@pytest.mark.asyncio
async def test_the_sweep_skips_a_profile_with_an_open_window(root: Path) -> None:
    open_profile = _profile(root, "live", marked=True)
    _open_window("live")

    assert await remove_orphaned_web_profiles() == 0
    assert open_profile.exists()


@pytest.mark.asyncio
async def test_the_sweep_survives_a_removal_error_and_continues(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> None:
    stuck = _profile(root, "a-stuck", marked=True)
    other = _profile(root, "b-fine", marked=True)
    real = web_windows.remove_profile_dir

    async def _remove(path: Path) -> None:
        if path.name == "a-stuck":
            raise PermissionError
        await real(path)

    monkeypatch.setattr(web_windows, "remove_profile_dir", _remove)

    assert await remove_orphaned_web_profiles() == 1
    assert stuck.exists()
    assert not other.exists()


@pytest.mark.asyncio
async def test_a_missing_root_is_nothing_to_sweep(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("core.web_login._tombstone.web_profiles_root", lambda: tmp_path / "none")

    assert await remove_orphaned_web_profiles() == 0


@pytest.mark.asyncio
async def test_a_failed_first_removal_is_finished_by_the_next_sweep(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> None:
    profile = _profile(root, "acc", marked=False)
    monkeypatch.setattr(_lifetime, "_REMOVE_RETRY_DELAY", 0)

    def _locked(_path: Path) -> None:
        raise PermissionError

    with monkeypatch.context() as patch:
        patch.setattr(_lifetime.shutil, "rmtree", _locked)
        await forget_account_web("acc")
    assert (profile / TOMBSTONE_NAME).is_file()  # marked before the failed removal

    assert await remove_orphaned_web_profiles() == 1
    assert not profile.exists()


@pytest.mark.asyncio
async def test_a_marker_write_failure_does_not_block_the_removal(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> None:
    profile = _profile(root, "acc", marked=False)

    def _boom(_path: Path) -> bool:
        raise PermissionError

    monkeypatch.setattr(web_windows, "mark_profile_for_removal", _boom)

    await forget_account_web("acc")

    assert not profile.exists()


@pytest.mark.asyncio
async def test_a_stale_mark_cleared_at_launch_keeps_the_readded_accounts_profile(
    root: Path,
) -> None:
    # Deleted (removal failed), then added again under the same id: the directory is reused.
    reused = _profile(root, "same-id", marked=True)

    clear_removal_mark(reused)
    clear_removal_mark(root / "never-opened")  # nothing there: not an error

    assert await remove_orphaned_web_profiles() == 0
    assert reused.exists()
