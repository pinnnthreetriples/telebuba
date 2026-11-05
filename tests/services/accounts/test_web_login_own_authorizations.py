"""A successful web login is recorded as Telebuba's own; a failed one records nothing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from services.accounts import _web_drive_run, web_login
from services.accounts.web_login import open_account_web
from tests.services.accounts._web_login_support import PROXY as _PROXY
from tests.services.accounts._web_login_support import fresh_registry, relay
from tests.services.accounts._web_login_support import patch_profile as _patch_profile
from tests.services.accounts._web_login_support import patch_proxy as _patch_proxy
from tests.services.accounts._web_login_support import settle_recorders as _settle_recorders
from tests.services.accounts._web_login_support import wire as _wire

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["fresh_registry", "relay"]


@pytest.mark.asyncio
async def test_a_successful_login_is_recorded_as_ours_since_the_drive_began(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["qr", "logged_in"])

    result = await open_account_web("acct")
    await _settle_recorders()

    assert result.signed_in is True
    assert [account for account, _since in calls["recorded"]] == ["acct"]
    assert calls["recorded"][0][1].tzinfo is not None


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

    async def _boom(_account_id: str, _since: object) -> int:
        raise RuntimeError

    monkeypatch.setattr(_web_drive_run, "_record_own_authorizations", _boom)

    result = await open_account_web("acct")
    await _settle_recorders()

    assert result.signed_in is True
