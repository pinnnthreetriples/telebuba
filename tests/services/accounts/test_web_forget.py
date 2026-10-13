"""A deleted account takes its web window, its relay and its signed-in profile with it.

The profile holds a live Telegram Web login, and an open window keeps using it, so
neither may outlive the account. The cleanup is best effort: a failure is logged by
code and never blocks or undoes the delete.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from core.web_login import _lifetime
from core.web_login._lifetime import remove_profile_dir
from schemas.accounts import AccountCreate
from services.accounts import add_account, remove_account, web_login, web_windows
from services.accounts.web_windows import forget_account_web
from tests.services.accounts._web_login_support import PROXY as _PROXY
from tests.services.accounts._web_login_support import FakeRelay as _FakeRelay
from tests.services.accounts._web_login_support import FakeWindow as _FakeWindow
from tests.services.accounts._web_login_support import fresh_registry

if TYPE_CHECKING:
    from pathlib import Path

# Re-bound so pytest collects the shared fixture in this module's namespace.
__all__ = ["fresh_registry"]


@pytest.fixture
def profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The account's signed-in profile, where the cleanup looks for it."""
    path = tmp_path / "web_profiles" / "acc"
    (path / "Default").mkdir(parents=True)
    (path / "Default" / "Cookies").write_text("telegram web session", encoding="utf-8")
    monkeypatch.setattr(web_windows, "account_profile_dir", lambda _account_id: path)
    return path


def _open_window(account_id: str) -> tuple[_FakeWindow, _FakeRelay]:
    window = _FakeWindow()
    relay = _FakeRelay(_PROXY)
    web_login._windows[account_id] = window  # ty: ignore[invalid-assignment]
    web_login._relays[account_id] = (relay, _PROXY)  # ty: ignore[invalid-assignment]
    return window, relay


@pytest.mark.asyncio
async def test_forgetting_ends_the_window_and_relay_and_deletes_the_profile(
    profile: Path,
) -> None:
    window, relay = _open_window("acc")
    await web_login._account_open_lock("acc")

    await forget_account_web("acc")

    assert window.killed is True
    assert relay.closed is True
    assert not profile.exists()
    assert "acc" not in web_login._windows
    assert "acc" not in web_login._relays
    assert "acc" not in web_login._open_locks


@pytest.mark.asyncio
async def test_a_relay_left_without_a_window_is_closed_too(profile: Path) -> None:
    relay = _FakeRelay(_PROXY)
    web_login._relays["acc"] = (relay, _PROXY)  # ty: ignore[invalid-assignment]

    await forget_account_web("acc")

    assert relay.closed is True
    assert not profile.exists()


@pytest.mark.asyncio
async def test_a_failed_cleanup_is_logged_by_code_and_never_raised(
    monkeypatch: pytest.MonkeyPatch,
    profile: Path,
) -> None:
    logged: list[tuple[str, str | None, dict[str, object] | None]] = []

    async def _log(
        _level: str, event: str, account_id: str | None = None, extra: Any = None
    ) -> None:
        logged.append((event, account_id, extra))

    async def _locked(_path: Path) -> None:
        msg = r"C:\secret\path is in use"
        raise PermissionError(msg)

    monkeypatch.setattr(web_windows, "log_event", _log)
    monkeypatch.setattr(web_windows, "remove_profile_dir", _locked)

    await forget_account_web("acc")

    # By code and exception TYPE only: the message can carry a host path.
    assert logged == [
        ("account_remove_web_cleanup_failed", "acc", {"error_type": "PermissionError"})
    ]
    assert profile.exists()


@pytest.mark.asyncio
async def test_deleting_an_account_closes_its_window_and_deletes_its_profile(
    profile: Path,
) -> None:
    await add_account(AccountCreate(account_id="acc", session_name="acc"))
    window, relay = _open_window("acc")

    await remove_account("acc")

    assert window.killed is True
    assert relay.closed is True
    assert not profile.exists()


# ------------------------------------------------------------------ core removal


@pytest.mark.asyncio
async def test_a_missing_profile_is_nothing_to_remove(tmp_path: Path) -> None:
    await remove_profile_dir(tmp_path / "never-opened")


@pytest.mark.asyncio
async def test_a_locked_profile_is_retried_then_reported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A just-killed Chrome's helpers can hold files open for a moment on Windows."""
    attempts: list[Path] = []

    def _locked(path: Path) -> None:
        attempts.append(path)
        msg = "in use"
        raise PermissionError(msg)

    monkeypatch.setattr(_lifetime.shutil, "rmtree", _locked)
    monkeypatch.setattr(_lifetime, "_REMOVE_RETRY_DELAY", 0)

    with pytest.raises(PermissionError):
        await remove_profile_dir(tmp_path)

    assert len(attempts) == _lifetime._REMOVE_TRIES
