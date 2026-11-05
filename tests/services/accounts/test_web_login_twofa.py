"""2FA through the web window must not be lost to timing, a slow check or a hiccup.

The stored password is sent to Telegram at most once per WINDOW, but every attempt that
never reached Telegram (no field laid out yet, the field not taking the text) is retried;
a slow ``auth.checkPassword`` is waited out rather than called a rejection; a transient
fault accepting the QR token retries the same token; and every drive ends in one
structured, secret-free log event.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from core.telegram_client import WebLoginTransientError
from core.web_login._cdp import CdpError
from services.accounts import _web_drive, _web_drive_password, _web_drive_run, web_login
from services.accounts.web_login import open_account_web
from tests.services.accounts._web_login_support import PROXY as _PROXY
from tests.services.accounts._web_login_support import FakeRelay as _FakeRelay  # noqa: F401
from tests.services.accounts._web_login_support import fresh_registry, relay  # noqa: F401
from tests.services.accounts._web_login_support import patch_profile as _patch_profile
from tests.services.accounts._web_login_support import patch_proxy as _patch_proxy
from tests.services.accounts._web_login_support import wire as _wire

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.usefixtures("relay")


def _setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    return calls


def _fake_type(
    monkeypatch: pytest.MonkeyPatch,
    calls: dict[str, Any],
    first: tuple[bool, ...] = (),
    then: bool = True,  # noqa: FBT001, FBT002
) -> None:
    """``type_2fa_password`` answering ``answers`` in turn, then ``then``; calls recorded."""
    queue = list(first)

    async def _type(_window: object, password: str) -> bool:
        calls.setdefault("typed", []).append(password)
        return queue.pop(0) if queue else then

    monkeypatch.setattr(_web_drive_password, "type_2fa_password", _type)


@pytest.mark.asyncio
async def test_a_field_that_shows_up_late_is_typed_until_sent_and_then_exactly_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """WebK slides the password page in: the first polls find no field to click."""
    calls = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(_web_drive_password, "_PASSWORD_GRACE", 1000.0)
    _wire(
        monkeypatch,
        calls,
        states=["qr", "password", "password", "password", "password", "password", "logged_in"],
        twofa="hunter2",
    )
    _fake_type(monkeypatch, calls, first=(False, False))

    result = await open_account_web("acct")

    assert result.signed_in is True
    # Two attempts that reached nothing, ONE that sent it, and no fourth once it was sent.
    assert calls["typed"] == ["hunter2"] * 3


@pytest.mark.asyncio
async def test_a_redrive_types_when_the_first_drive_sent_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _setup(monkeypatch, tmp_path)
    _wire(monkeypatch, calls, states=["password"], twofa="hunter2")
    _fake_type(monkeypatch, calls, then=False)

    first = await open_account_web("acct")

    assert first.signed_in is False
    assert first.reason == "web_login_2fa_required"  # nothing reached Telegram
    sent_before = len(calls["typed"])

    _fake_type(monkeypatch, calls)  # the field is there this time
    second = await open_account_web("acct")

    assert calls["focused"] == 1  # the raised-window path
    assert len(calls["typed"]) == sent_before + 1  # typed on the re-drive, once
    assert second.reason == "web_login_2fa_rejected"  # and THIS time it really was sent


@pytest.mark.asyncio
async def test_a_slow_password_check_is_waited_out_not_called_a_rejection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(_web_drive_password, "_PASSWORD_CHECK_MAX", 1000.0)
    _wire(
        monkeypatch,
        calls,
        states=["qr", "password", "password", "password", "password", "password", "logged_in"],
        twofa="hunter2",
    )

    async def _checking(_window: object) -> str:
        return "checking"

    monkeypatch.setattr(_web_drive_password, "password_outcome", _checking)

    result = await open_account_web("acct")

    assert result.signed_in is True  # the grace is 0, yet it waited: WebK was still checking
    assert calls["typed"] == ["hunter2"]


@pytest.mark.asyncio
async def test_a_wrong_password_state_ends_the_wait_at_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(_web_drive_password, "_PASSWORD_GRACE", 1000.0)
    _wire(monkeypatch, calls, states=["qr", "password"], twofa="hunter2")

    async def _error(_window: object) -> str:
        return "error"

    monkeypatch.setattr(_web_drive_password, "password_outcome", _error)

    result = await open_account_web("acct")

    assert result.signed_in is False
    assert result.reason == "web_login_2fa_rejected"
    assert calls["typed"] == ["hunter2"]


@pytest.mark.asyncio
async def test_still_checking_when_time_runs_out_is_not_blamed_on_the_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(web_login, "_DRIVE_TIMEOUT", 0.2)
    monkeypatch.setattr(_web_drive_password, "_PASSWORD_CHECK_MAX", 1000.0)
    _wire(monkeypatch, calls, states=["password"], twofa="hunter2")

    async def _checking(_window: object) -> str:
        return "checking"

    monkeypatch.setattr(_web_drive_password, "password_outcome", _checking)

    result = await open_account_web("acct")

    assert result.reason == "web_login_2fa_required"


@pytest.mark.asyncio
async def test_a_transient_accept_failure_retries_the_same_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _setup(monkeypatch, tmp_path)
    _wire(monkeypatch, calls, states=["qr", "qr", "qr", "logged_in"])
    failures = [WebLoginTransientError(0.0)]

    async def _accept(_account_id: str, token_bytes: bytes) -> bool:
        if failures:
            raise failures.pop()
        calls.setdefault("accepted", []).append(token_bytes)
        return True

    monkeypatch.setattr(_web_drive, "accept_web_login_token", _accept)

    result = await open_account_web("acct")

    assert result.signed_in is True
    assert calls["accepted"] == [b"token1"]  # the same token, offered again


@pytest.mark.asyncio
async def test_a_cdp_hiccup_on_a_live_window_does_not_abort_the_drive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _setup(monkeypatch, tmp_path)
    _wire(monkeypatch, calls, states=["logged_in"])
    failures = [CdpError("timed out")]

    async def _state(_window: object) -> str:
        if failures:
            raise failures.pop()
        return "logged_in"

    monkeypatch.setattr(_web_drive, "page_state", _state)

    result = await open_account_web("acct")

    assert result.signed_in is True


def _capture_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict[str, Any]]]:
    seen: list[tuple[str, str, dict[str, Any]]] = []

    async def _log(
        level: str,
        event: str,
        account_id: str | None = None,  # noqa: ARG001
        extra: dict[str, object] | None = None,
    ) -> None:
        seen.append((level, event, dict(extra or {})))

    monkeypatch.setattr(_web_drive_run, "log_event", _log)
    monkeypatch.setattr(_web_drive, "log_event", _log)
    return seen


@pytest.mark.asyncio
async def test_a_success_is_logged_without_any_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _setup(monkeypatch, tmp_path)
    events = _capture_events(monkeypatch)
    _wire(monkeypatch, calls, states=["qr", "password", "password", "logged_in"], twofa="hunter2")
    monkeypatch.setattr(_web_drive_password, "_PASSWORD_GRACE", 1000.0)

    await open_account_web("acct")

    (level, event, extra) = events[-1]
    assert (level, event) == ("INFO", "web_login_success")
    assert extra["typed_password"] is True
    assert extra["tokens_accepted"] == 1
    assert extra["started_at"]
    flat = repr(events)
    assert "hunter2" not in flat
    assert "dG9rZW4x" not in flat  # the captured token, base64url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "event"),
    [
        ((["qr"], None, None), "web_login_qr_timeout"),
        ((["loading"], None, None), "web_login_page_not_loaded"),
        ((["password"], None, None), "web_login_2fa_required"),
        ((["password"], "hunter2", "never"), "web_login_2fa_not_typed"),
        ((["password"], "hunter2", "sent"), "web_login_2fa_rejected"),
    ],
)
async def test_every_failed_drive_logs_its_own_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: tuple[list[str], str | None, str | None],
    event: str,
) -> None:
    states, twofa, typing = scenario
    calls = _setup(monkeypatch, tmp_path)
    events = _capture_events(monkeypatch)
    monkeypatch.setattr(web_login, "_DRIVE_TIMEOUT", 0.2)
    _wire(monkeypatch, calls, states=states, twofa=twofa, token=None)
    if typing is not None:
        _fake_type(monkeypatch, calls, then=typing == "sent")

    await open_account_web("acct")

    last = events[-1]
    assert last[:2] == ("WARNING", event)
    assert "hunter2" not in repr(events)


@pytest.mark.asyncio
async def test_a_drive_that_raises_is_logged_as_a_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _setup(monkeypatch, tmp_path)
    events = _capture_events(monkeypatch)
    _wire(monkeypatch, calls, states=["qr"])

    async def _boom(_account_id: str, _token: bytes) -> bool:
        raise RuntimeError

    monkeypatch.setattr(_web_drive, "accept_web_login_token", _boom)

    with pytest.raises(web_login.WebLoginLaunchError):
        await open_account_web("acct")

    assert events[-1][1] == "web_login_refused"
    assert events[-1][2]["error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_a_silent_capture_hook_is_logged_once_as_a_drift_indicator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _setup(monkeypatch, tmp_path)
    events = _capture_events(monkeypatch)
    monkeypatch.setattr(web_login, "_DRIVE_TIMEOUT", 0.2)
    monkeypatch.setattr(_web_drive, "_NO_TOKEN_WARN_SECONDS", 0.0)
    _wire(monkeypatch, calls, states=["qr"], token=None)

    await open_account_web("acct")

    names = [name for _level, name, _extra in events]
    assert names.count("web_login_no_token_captured") == 1
