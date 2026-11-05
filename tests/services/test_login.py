"""Tests for the phone-code login service (gateway faked, real TTL cache + DB)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    configure_database,
    fetch_account_twofa_password,
    set_account_twofa_password,
    update_account_from_session_check,
)
from schemas.accounts import AccountCreate
from schemas.phone_login import PhoneCodeChallenge, PhoneCodeRequest, PhoneCodeSubmit
from schemas.telegram_session import SessionCheckStatus, TelegramSessionCheckResult
from services.accounts import add_account
from services.accounts import login as login_service
from services.accounts._login_state import _PENDING
from services.accounts._result import AccountActionError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from schemas.device_fingerprint import TelegramClientRequest


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    _PENDING.clear()
    yield
    _PENDING.clear()


async def _account_with_phone(account_id: str, phone: str | None = "79990001122") -> None:
    await add_account(AccountCreate(account_id=account_id))
    if phone is not None:
        await update_account_from_session_check(
            TelegramSessionCheckResult(
                account_id=account_id,
                session_path="x",
                status="alive",
                is_temporary=False,
                phone=phone,
            ),
        )


def _fake_request(  # noqa: PLR0913 - one knob per gateway field
    monkeypatch: pytest.MonkeyPatch,
    *,
    hash_value: str,
    error: str | None,
    error_code: str | None = None,
    flood_wait_seconds: int | None = None,
    captured: list[PhoneCodeRequest] | None = None,
) -> None:
    async def _request(request: PhoneCodeRequest) -> PhoneCodeChallenge:
        if captured is not None:
            captured.append(request)
        return PhoneCodeChallenge(
            account_id=request.account_id,
            phone=request.phone,
            phone_code_hash=hash_value,
            error=error,
            error_code=error_code,
            flood_wait_seconds=flood_wait_seconds,
        )

    monkeypatch.setattr(login_service, "request_phone_code", _request)


def _fake_submit(  # noqa: PLR0913 - one knob per gateway field
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: SessionCheckStatus,
    error: str | None = None,
    error_type: str | None = None,
    flood_wait_seconds: int | None = None,
    captured: list[PhoneCodeSubmit] | None = None,
) -> None:
    async def _submit(request: PhoneCodeSubmit) -> TelegramSessionCheckResult:
        if captured is not None:
            captured.append(request)
        return TelegramSessionCheckResult(
            account_id=request.account_id,
            session_path="x",
            status=status,
            is_temporary=False,
            phone="79990001122",
            error_message=error,
            error_type=error_type,
            flood_wait_seconds=flood_wait_seconds,
        )

    monkeypatch.setattr(login_service, "submit_phone_code", _submit)


@pytest.mark.asyncio
async def test_start_phone_login_creates_account_with_phone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str, str | None]] = []

    async def log(level: str, event: str, *, account_id: str | None = None) -> None:
        events.append((level, event, account_id))

    monkeypatch.setattr(login_service, "log_event", log)
    account = await login_service.start_phone_login("+7 999 000-11-22", label="Primary")

    assert account.account_id == "79990001122"
    assert account.session_name == "79990001122"
    assert account.phone == "+7 999 000-11-22"
    assert account.label == "Primary"
    assert account.status == "new"
    assert events == [("INFO", "phone_login_started", "79990001122")]


@pytest.mark.asyncio
async def test_start_phone_login_then_request_code_has_phone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = await login_service.start_phone_login("+79990001122")
    _fake_request(monkeypatch, hash_value="HASH", error=None)

    result = await login_service.request_login_code(account.account_id)

    assert result.phone == "+79990001122"


@pytest.mark.asyncio
async def test_start_phone_login_duplicate_errors() -> None:
    await login_service.start_phone_login("+79990001122")

    with pytest.raises(login_service.SessionAlreadyExistsError):
        await login_service.start_phone_login("79990001122")


@pytest.mark.asyncio
async def test_start_phone_login_without_digits_errors() -> None:
    with pytest.raises(login_service.PhoneLoginError):
        await login_service.start_phone_login("no-digits")


@pytest.mark.asyncio
async def test_request_login_code_caches_the_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    await _account_with_phone("acc")
    requested: list[PhoneCodeRequest] = []
    submitted: list[PhoneCodeSubmit] = []
    _fake_request(monkeypatch, hash_value="HASH", error=None, captured=requested)
    _fake_submit(monkeypatch, status="alive", captured=submitted)

    result = await login_service.request_login_code("acc")
    account = await login_service.submit_login_code("acc", "01234", password="2fa")

    assert result.phone == "79990001122"
    assert account.status == "alive"
    assert [request.model_dump() for request in requested] == [
        {"account_id": "acc", "session_name": None, "phone": "79990001122"},
    ]
    assert [submission.model_dump() for submission in submitted] == [
        {
            "account_id": "acc",
            "session_name": None,
            "phone": "79990001122",
            "phone_code_hash": "HASH",
            "code": "01234",
            "password": "2fa",
        },
    ]


@pytest.mark.asyncio
async def test_request_login_code_without_phone_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    await _account_with_phone("acc", phone=None)
    _fake_request(monkeypatch, hash_value="HASH", error=None)

    with pytest.raises(login_service.PhoneLoginError):
        await login_service.request_login_code("acc")


@pytest.mark.asyncio
async def test_request_login_code_unknown_account_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_request(monkeypatch, hash_value="HASH", error=None)
    with pytest.raises(login_service.PhoneLoginError):
        await login_service.request_login_code("nope")


@pytest.mark.asyncio
async def test_request_login_code_surfaces_gateway_error(monkeypatch: pytest.MonkeyPatch) -> None:
    await _account_with_phone("acc")
    _fake_request(
        monkeypatch,
        hash_value="",
        error="flood wait 30s",
        error_code="flood_wait",
        flood_wait_seconds=30,
    )

    with pytest.raises(AccountActionError) as excinfo:
        await login_service.request_login_code("acc")
    assert (str(excinfo.value), excinfo.value.retry_after_seconds) == ("flood_wait", 30)


@pytest.mark.asyncio
async def test_request_login_code_timeout_is_the_stable_unavailable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account_with_phone("acc")
    _fake_request(monkeypatch, hash_value="", error="TimeoutError", error_code="unavailable")

    with pytest.raises(AccountActionError) as excinfo:
        await login_service.request_login_code("acc")
    assert str(excinfo.value) == "unavailable"


@pytest.mark.asyncio
async def test_submit_without_pending_code_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    await _account_with_phone("acc")
    _fake_submit(monkeypatch, status="alive")

    with pytest.raises(AccountActionError, match="no_pending_code"):
        await login_service.submit_login_code("acc", "11111")


@pytest.mark.asyncio
async def test_submit_login_code_signs_in_and_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    await _account_with_phone("acc")
    _fake_request(monkeypatch, hash_value="HASH", error=None)
    _fake_submit(monkeypatch, status="alive")
    await login_service.request_login_code("acc")

    account = await login_service.submit_login_code("acc", "11111")

    assert account.status == "alive"
    with pytest.raises(AccountActionError, match="no_pending_code"):
        await login_service.submit_login_code("acc", "11111")


@pytest.mark.asyncio
async def test_submit_login_code_bad_code_keeps_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    await _account_with_phone("acc")
    _fake_request(monkeypatch, hash_value="HASH", error=None)
    _fake_submit(
        monkeypatch,
        status="unauthorized",
        error="PhoneCodeInvalidError",
        error_type="PhoneCodeInvalidError",
    )
    await login_service.request_login_code("acc")

    with pytest.raises(AccountActionError, match="code_invalid"):
        await login_service.submit_login_code("acc", "00000")
    _fake_submit(monkeypatch, status="alive")
    account = await login_service.submit_login_code("acc", "11111")
    assert account.status == "alive", "a wrong code must leave the challenge retryable"


@pytest.mark.asyncio
async def test_pending_code_expires_at_exact_ttl_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account_with_phone("acc")
    _fake_request(monkeypatch, hash_value="HASH", error=None)
    _fake_submit(monkeypatch, status="alive")
    monkeypatch.setattr(settings.telegram, "phone_code_ttl_seconds", 0.0)
    monkeypatch.setattr(login_service.time, "monotonic", lambda: 1_000.0)

    await login_service.request_login_code("acc")

    with pytest.raises(AccountActionError, match="no_pending_code"):
        await login_service.submit_login_code("acc", "11111")


async def _refusal_code(
    monkeypatch: pytest.MonkeyPatch,
    status: SessionCheckStatus,
    error_type: str | None = None,
    flood_wait_seconds: int | None = None,
) -> AccountActionError:
    await _account_with_phone("acc")
    _fake_request(monkeypatch, hash_value="HASH", error=None)
    _fake_submit(
        monkeypatch,
        status=status,
        error_type=error_type,
        flood_wait_seconds=flood_wait_seconds,
    )
    await login_service.request_login_code("acc")
    with pytest.raises(AccountActionError) as excinfo:
        await login_service.submit_login_code("acc", "11111")
    return excinfo.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type", "code"),
    [
        ("unauthorized", "SessionPasswordNeededError", "password_needed"),
        ("unauthorized", "PasswordHashInvalidError", "password_invalid"),
        ("unauthorized", "PhoneCodeExpiredError", "code_expired"),
        ("network_error", "TimeoutError", "unavailable"),
        ("proxy_error", "ProxyError", "unavailable"),
        ("unknown_error", "PhoneNumberBannedError", "failed"),
    ],
)
async def test_submit_refusals_are_stable_codes(
    monkeypatch: pytest.MonkeyPatch,
    status: SessionCheckStatus,
    error_type: str,
    code: str,
) -> None:
    exc = await _refusal_code(monkeypatch, status, error_type)
    assert str(exc) == code


@pytest.mark.asyncio
async def test_submit_flood_wait_carries_the_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    exc = await _refusal_code(monkeypatch, "flood_wait", flood_wait_seconds=42)
    assert (str(exc), exc.retry_after_seconds) == ("flood_wait", 42)


@pytest.mark.asyncio
async def test_an_expired_code_drops_the_pending_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    await _refusal_code(monkeypatch, "unauthorized", "PhoneCodeExpiredError")
    assert "acc" not in _PENDING


@pytest.mark.asyncio
async def test_a_verified_password_is_stored_for_the_second_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account_with_phone("acc")
    _fake_request(monkeypatch, hash_value="HASH", error=None)
    _fake_submit(monkeypatch, status="alive")
    await login_service.request_login_code("acc")

    await login_service.submit_login_code("acc", "11111", password="cloud-pw ")

    assert await fetch_account_twofa_password("acc") == "cloud-pw "


@pytest.mark.asyncio
async def test_no_typed_password_stores_nothing_and_falls_back_to_the_stored_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account_with_phone("acc")
    await set_account_twofa_password("acc", "stored-pw")
    submitted: list[PhoneCodeSubmit] = []
    _fake_request(monkeypatch, hash_value="HASH", error=None)
    _fake_submit(monkeypatch, status="alive", captured=submitted)
    await login_service.request_login_code("acc")

    await login_service.submit_login_code("acc", "11111")

    assert [s.password for s in submitted] == ["stored-pw"]
    assert await fetch_account_twofa_password("acc") == "stored-pw"


@pytest.mark.asyncio
async def test_the_response_does_not_wait_for_the_inbox_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account_with_phone("acc")
    _fake_request(monkeypatch, hash_value="HASH", error=None)
    _fake_submit(monkeypatch, status="alive")
    release = asyncio.Event()
    started: list[str] = []

    async def _blocked_start(account_id: str) -> None:
        started.append(account_id)
        await release.wait()

    monkeypatch.setattr(login_service, "start_account_inbox", _blocked_start)
    await login_service.request_login_code("acc")

    account = await asyncio.wait_for(login_service.submit_login_code("acc", "11111"), timeout=2)

    assert account.status == "alive"
    await asyncio.sleep(0)
    assert started == ["acc"]
    release.set()
    await asyncio.gather(*login_service._INBOX_STARTS)


@pytest.mark.asyncio
async def test_logout_marks_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    await _account_with_phone("acc")

    captured: dict[str, object] = {}

    async def _logout(
        request: TelegramClientRequest,
        *,
        wipe_session: bool,
    ) -> TelegramSessionCheckResult:
        captured["wipe"] = wipe_session
        return TelegramSessionCheckResult(
            account_id=request.account_id,
            session_path="x",
            status="unauthorized",
            is_temporary=False,
        )

    monkeypatch.setattr(login_service, "log_out_session", _logout)

    account = await login_service.logout_account("acc")

    assert account.status == "unauthorized"
    assert captured["wipe"] is False


@pytest.mark.asyncio
async def test_reset_session_wipes_session(monkeypatch: pytest.MonkeyPatch) -> None:
    await _account_with_phone("acc")

    captured: dict[str, object] = {}

    async def _logout(
        request: TelegramClientRequest,
        *,
        wipe_session: bool,
    ) -> TelegramSessionCheckResult:
        captured["wipe"] = wipe_session
        return TelegramSessionCheckResult(
            account_id=request.account_id,
            session_path="x",
            status="unauthorized",
            is_temporary=False,
        )

    monkeypatch.setattr(login_service, "log_out_session", _logout)

    account = await login_service.reset_account_session("acc")

    assert account.status == "unauthorized"
    assert captured["wipe"] is True
