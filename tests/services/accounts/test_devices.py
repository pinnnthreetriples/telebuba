"""An account's devices: chosen at creation, added on request, removed with the account.

Desktop is the default everywhere; mobile exists only by the operator's explicit pick,
and a mobile pick without the Android app credentials is refused before anything is
created. Adding the other device is one paced sign-in per account, and deleting the
account takes both ``.session`` files and every fingerprint row with it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import fetch_account, fetch_device_fingerprint
from core.device_fingerprint_android import generate_android_fingerprint
from core.repositories.device_fingerprint import record_second_device
from core.telegram_client import _pool, account_auth_lock
from schemas.accounts import AccountCheckRequest, AccountCreate, AccountSessionFileImport
from schemas.telegram_session import TelegramSessionCheckRequest, TelegramSessionCheckResult
from services.accounts import (
    DeviceRefusedError,
    add_account,
    add_account_device,
    check_account_session,
    import_account_session,
    logout_account,
    remove_account,
    reset_account_session,
    start_phone_login,
)
from services.accounts import devices as devices_module
from services.accounts._import_rollback import discard_imported_session
from tests.services.accounts._mobile_support import _fake_sign_in

if TYPE_CHECKING:
    from schemas.device_fingerprint import TelegramClientRequest

_PHONE = "+5215550001111"


@pytest.fixture(autouse=True)
def _mobile_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.telegram, "mobile_api_id", 6)
    monkeypatch.setattr(settings.telegram, "mobile_api_hash", "mobile-hash")
    devices_module._LAST_CODE_REQUEST.clear()


def _session_file(name: str) -> Path:
    return Path(settings.telegram.session_dir) / f"{name}.session"


async def _desktop(account_id: str = "acc", phone: str | None = _PHONE) -> None:
    await add_account(AccountCreate(account_id=account_id, session_name=account_id, phone=phone))


@pytest.mark.asyncio
async def test_adding_mobile_signs_in_a_separate_session(monkeypatch: pytest.MonkeyPatch) -> None:
    await _desktop()
    calls = _fake_sign_in(monkeypatch)

    account = await add_account_device("acc", "mobile")

    assert [(c.device_type, c.session_name) for c in calls] == [("mobile", "acc_mobile")]
    assert account.devices == ["desktop", "mobile"]
    assert account.session_name == "acc", "the existing session is untouched"


@pytest.mark.asyncio
async def test_a_second_code_request_waits_out_the_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stamped even when the attempt fails: the code may already have been sent."""
    await _desktop()
    _fake_sign_in(monkeypatch, code="device_code_timeout")
    with pytest.raises(DeviceRefusedError):
        await add_account_device("acc", "mobile")
    calls = _fake_sign_in(monkeypatch)

    with pytest.raises(DeviceRefusedError) as refused:
        await add_account_device("acc", "mobile")

    assert refused.value.code == "device_code_cooldown"
    assert 0 < (refused.value.retry_after_seconds or 0) <= 180
    assert calls == [], "Telegram is not asked again inside the cooldown"


@pytest.mark.asyncio
async def test_a_gateway_refusal_keeps_its_code_and_flood_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _desktop()
    _fake_sign_in(monkeypatch, code="device_flood_wait", flood=90)

    with pytest.raises(DeviceRefusedError) as refused:
        await add_account_device("acc", "mobile")

    assert (refused.value.code, refused.value.retry_after_seconds) == ("device_flood_wait", 90)
    account = await fetch_account("acc")
    assert account is not None
    assert account.devices == ["desktop"]


@pytest.mark.parametrize(
    ("setup", "device", "code"),
    [
        ("present", "desktop", "device_already_present"),
        ("no_mobile_api", "mobile", "mobile_api_not_configured"),
    ],
)
@pytest.mark.asyncio
async def test_what_cannot_work_is_refused_before_telegram(
    monkeypatch: pytest.MonkeyPatch,
    setup: str,
    device: str,
    code: str,
) -> None:
    await _desktop()
    if setup == "no_mobile_api":
        monkeypatch.setattr(settings.telegram, "mobile_api_hash", "")
    calls = _fake_sign_in(monkeypatch)

    with pytest.raises(DeviceRefusedError) as refused:
        await add_account_device("acc", device)  # ty: ignore[invalid-argument-type]

    assert refused.value.code == code
    assert calls == []


@pytest.mark.asyncio
async def test_a_mobile_session_import_is_a_mobile_only_account() -> None:
    account = await import_account_session(
        AccountSessionFileImport(
            filename="phone.session",
            content=b"android auth key",
            session_device="mobile",
        ),
    )

    assert (account.session_device, account.devices) == ("mobile", ["mobile"])
    mobile = await fetch_device_fingerprint("phone", "mobile")
    assert mobile is not None
    assert mobile.platform == "android"
    assert await fetch_device_fingerprint("phone", "desktop") is None, "no desktop is invented"


@pytest.mark.asyncio
async def test_an_import_defaults_to_desktop() -> None:
    account = await import_account_session(
        AccountSessionFileImport(filename="desk.session", content=b"desktop auth key"),
    )

    assert account.devices == ["desktop"]
    desktop = await fetch_device_fingerprint("desk", "desktop")
    assert desktop is not None
    assert desktop.platform != "android"


@pytest.mark.asyncio
async def test_a_mobile_pick_without_the_android_credentials_creates_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.telegram, "mobile_api_id", 0)

    with pytest.raises(DeviceRefusedError) as refused:
        await import_account_session(
            AccountSessionFileImport(
                filename="phone.session",
                content=b"android auth key",
                session_device="mobile",
            ),
        )
    with pytest.raises(DeviceRefusedError):
        await start_phone_login(_PHONE, session_device="mobile")

    assert refused.value.code == "mobile_api_not_configured"
    assert not _session_file("phone").exists()
    assert await fetch_account("phone") is None
    assert await fetch_account("5215550001111") is None


@pytest.mark.asyncio
async def test_a_phone_login_signs_in_as_the_picked_device() -> None:
    desktop = await start_phone_login("+1 555 000 2222")
    mobile = await start_phone_login("+1 555 000 3333", session_device="mobile")

    assert desktop.devices == ["desktop"]
    assert mobile.devices == ["mobile"]
    assert await fetch_device_fingerprint(mobile.account_id, "mobile") is not None


@pytest.mark.asyncio
async def test_removing_the_account_takes_both_sessions_and_fingerprints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _desktop()
    _fake_sign_in(monkeypatch)
    await add_account_device("acc", "mobile")
    for name in ("acc", "acc_mobile"):
        _session_file(name).parent.mkdir(parents=True, exist_ok=True)
        _session_file(name).write_bytes(b"auth key")

    await remove_account("acc")

    assert not _session_file("acc").exists()
    assert not _session_file("acc_mobile").exists()
    assert await fetch_device_fingerprint("acc", "desktop") is None
    assert await fetch_device_fingerprint("acc", "mobile") is None
    assert "acc" not in devices_module._LAST_CODE_REQUEST


@pytest.mark.asyncio
async def test_an_import_rollback_removes_both_session_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _desktop()
    _fake_sign_in(monkeypatch)
    await add_account_device("acc", "mobile")
    for name in ("acc", "acc_mobile"):
        _session_file(name).parent.mkdir(parents=True, exist_ok=True)
        _session_file(name).write_bytes(b"auth key")

    result = await discard_imported_session("acc", _session_file("acc"))

    assert result.outcome == "clean"
    assert not _session_file("acc").exists()
    assert not _session_file("acc_mobile").exists()
    assert await fetch_account("acc") is None


def _unauthorized(account_id: str) -> TelegramSessionCheckResult:
    return TelegramSessionCheckResult(
        account_id=account_id,
        session_path=account_id,
        status="unauthorized",
        is_temporary=False,
    )


@pytest.mark.parametrize("end", [logout_account, reset_account_session])
@pytest.mark.asyncio
async def test_ending_the_session_signs_the_second_device_out_and_forgets_it(
    monkeypatch: pytest.MonkeyPatch,
    end: object,
) -> None:
    """A logout or reset ends every sign-in: a forgotten second device can be added again."""
    await _desktop()
    _fake_sign_in(monkeypatch)
    await add_account_device("acc", "mobile")
    logged_out: list[tuple[str | None, bool]] = []

    async def fake_log_out(request: TelegramClientRequest, *, wipe_session: bool):
        logged_out.append((request.session_name, wipe_session))
        return _unauthorized(request.account_id)

    monkeypatch.setattr("services.accounts.login.log_out_session", fake_log_out)
    monkeypatch.setattr("core.telegram_client._second_device.log_out_session", fake_log_out)

    account = await end("acc")  # ty: ignore[call-non-callable]

    assert ("acc_mobile", True) in logged_out
    assert account.devices == ["desktop"]
    assert account.second_session_name is None
    assert await fetch_device_fingerprint("acc", "mobile") is None
    assert await fetch_device_fingerprint("acc", "desktop") is not None


@pytest.mark.asyncio
async def test_the_session_check_probes_the_accounts_own_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check probes the account's own session — the one phone login signs in again.

    Probing the added desktop instead (the primary) made a check and a re-login act on
    two different sessions: the check marked the row dead, the login revived the other
    session, and the next check marked it dead again.
    """
    await add_account(AccountCreate(account_id="ph", session_name="ph", session_device="mobile"))
    await record_second_device(
        "ph_desktop",
        generate_android_fingerprint("ph", None).model_copy(
            update={"device_type": "desktop", "platform": "windows", "device_model": "Desktop PC"},
        ),
    )
    probed: list[str | None] = []

    async def fake_check(request: TelegramSessionCheckRequest) -> TelegramSessionCheckResult:
        probed.append(request.session_name)
        return _unauthorized(request.account_id)

    monkeypatch.setattr("services.accounts.sessions.check_telegram_session", fake_check)

    await check_account_session(AccountCheckRequest(account_id="ph"))

    assert probed == ["ph"]


@pytest.mark.asyncio
async def test_a_recreated_account_does_not_inherit_a_stale_primary_device() -> None:
    _pool._PRIMARY["phone"] = "desktop"

    await import_account_session(
        AccountSessionFileImport(filename="phone.session", content=b"key", session_device="mobile"),
    )

    assert "phone" not in _pool._PRIMARY


@pytest.mark.asyncio
async def test_a_refusal_before_any_code_was_asked_for_keeps_the_pacing_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _desktop()
    _fake_sign_in(monkeypatch, code="device_primary_unavailable")
    with pytest.raises(DeviceRefusedError):
        await add_account_device("acc", "mobile")
    calls = _fake_sign_in(monkeypatch)

    await add_account_device("acc", "mobile")

    assert len(calls) == 1, "the retry is not blocked by a cooldown no code started"


@pytest.mark.asyncio
async def test_a_failed_second_device_removal_still_writes_the_logout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _desktop()
    _fake_sign_in(monkeypatch)
    await add_account_device("acc", "mobile")

    async def fake_log_out(request: TelegramClientRequest, *, wipe_session: bool):
        assert wipe_session is False
        return _unauthorized(request.account_id)

    async def broken_removal(_account_id: str) -> None:
        raise PermissionError

    monkeypatch.setattr("services.accounts.login.log_out_session", fake_log_out)
    monkeypatch.setattr("services.accounts.login.remove_second_device", broken_removal)

    account = await logout_account("acc")

    assert account.status == "unauthorized"


@pytest.mark.asyncio
async def test_removal_holds_the_accounts_auth_lock_while_it_unlinks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No second-device sign-in can record a session between the read and the delete."""
    await _desktop()
    held: list[bool] = []

    async def unlink(account_id: str, _session_name: str | None = None) -> None:
        held.append(account_auth_lock(account_id).locked())

    monkeypatch.setattr("services.accounts.lifecycle.remove_account_session", unlink)

    await remove_account("acc")

    assert held == [True]
    assert await fetch_account("acc") is None


@pytest.mark.parametrize(
    ("elapsed", "left"),
    [(179.0, 1), (179.5, 1), (179.999, 1), (180.0, None), (181.0, None)],
)
def test_the_code_pause_ends_on_its_second_and_rounds_what_is_left_up(
    monkeypatch: pytest.MonkeyPatch, elapsed: float, left: int | None
) -> None:
    monkeypatch.setattr(settings.telegram, "second_device_code_cooldown_seconds", 180)
    devices_module._LAST_CODE_REQUEST["acc"] = 1000.0
    monkeypatch.setattr(devices_module.time, "monotonic", lambda: 1000.0 + elapsed)

    if left is None:
        devices_module.claim_code_request("acc")
        assert devices_module._LAST_CODE_REQUEST["acc"] == 1000.0 + elapsed
        return
    with pytest.raises(DeviceRefusedError) as refused:
        devices_module.claim_code_request("acc")
    assert refused.value.retry_after_seconds == left
