"""A successful web login is recorded as Telebuba's own; a failed one records nothing."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

import pytest

from core.config import settings
from services.accounts import _web_drive_run, own_authorizations, web_login
from services.accounts.web_login import open_account_web
from tests.services.accounts._web_login_support import PROXY as _PROXY
from tests.services.accounts._web_login_support import fresh_registry, relay
from tests.services.accounts._web_login_support import patch_page_state as _patch_page_state
from tests.services.accounts._web_login_support import patch_profile as _patch_profile
from tests.services.accounts._web_login_support import patch_proxy as _patch_proxy
from tests.services.accounts._web_login_support import scripted_states as _scripted_states
from tests.services.accounts._web_login_support import settle_recorders as _settle_recorders
from tests.services.accounts._web_login_support import wire as _wire

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["fresh_registry", "relay"]


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_successful_login_is_recorded_with_the_hash_its_qr_accept_returned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["qr", "logged_in"])

    result = await open_account_web("acct")
    await _settle_recorders()

    assert result.signed_in is True
    assert calls["recorded"] == [("acct", "777")]  # the hash Telegram gave for the QR accept


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_failed_login_records_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["qr"])
    monkeypatch.setattr(web_login, "_DRIVE_TIMEOUT", 0.05)

    result = await open_account_web("acct")
    await _settle_recorders()

    assert result.signed_in is False
    assert "recorded" not in calls


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_recording_that_fails_never_fails_the_login(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["qr", "logged_in"])

    async def _boom(*_args: object) -> int:
        raise RuntimeError

    monkeypatch.setattr(_web_drive_run, "_record_own_authorizations", _boom)

    result = await open_account_web("acct")
    await _settle_recorders()

    assert result.signed_in is True


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_the_baseline_is_taken_for_the_unsigned_window_and_handed_to_the_recorder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    window = _wire(monkeypatch, calls, states=["qr", "logged_in"])

    await open_account_web("acct")
    await _settle_recorders()

    assert calls["baselines"] == [("acct", window)]
    assert calls["recorded_windows"] == [window]  # judged against THAT window's baseline


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_login_finished_by_hand_after_a_timeout_is_recorded_by_the_redrive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    window = _wire(monkeypatch, calls, states=["qr"])
    monkeypatch.setattr(web_login, "_DRIVE_TIMEOUT", 0.05)

    first = await open_account_web("acct")
    await _settle_recorders()
    assert first.signed_in is False
    assert "recorded" not in calls

    _patch_page_state(monkeypatch, _scripted_states(["logged_in"]))  # the operator finished it
    second = await open_account_web("acct")
    await _settle_recorders()

    assert second.signed_in is True
    assert calls["recorded_windows"] == [window]  # the raised window, with its launch baseline
    assert calls["recorded"] == [("acct", "777")]  # the first drive's accepted hash survives
    assert len(calls["baselines"]) == 1


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_shutdown_cancels_a_recording_still_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["qr", "logged_in"])
    started = asyncio.Event()

    async def _hang(*_args: object) -> int:
        started.set()
        await asyncio.Event().wait()
        return 0

    monkeypatch.setattr(_web_drive_run, "_record_own_authorizations", _hang)

    await open_account_web("acct")
    await asyncio.wait_for(started.wait(), 5)
    assert _web_drive_run._recorders

    await web_login.shutdown_web_login()

    assert not _web_drive_run._recorders


@pytest.mark.asyncio
async def test_a_hung_baseline_listing_never_holds_the_window_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def _hang(_account_id: str, _window: object) -> bool:
        await asyncio.Event().wait()
        return True

    async def _log(_level: str, event: str, **_kwargs: object) -> None:
        events.append(event)

    monkeypatch.setattr(own_authorizations, "remember_login_baseline", _hang)
    monkeypatch.setattr(_web_drive_run, "log_event", _log)
    monkeypatch.setattr(settings.session_audit, "own_login_baseline_timeout_seconds", 0.05)

    await asyncio.wait_for(_web_drive_run.take_login_baseline("acct", cast("Any", object())), 5)

    assert events == ["own_web_baseline_failed"]
