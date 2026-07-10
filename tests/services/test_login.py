"""Tests for the phone-code login service (gateway faked, real TTL cache + DB)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database, update_account_from_session_check
from schemas.accounts import AccountCreate
from schemas.phone_login import PhoneCodeChallenge, PhoneCodeRequest, PhoneCodeSubmit
from schemas.telegram_session import SessionCheckStatus, TelegramSessionCheckResult
from services.accounts import add_account
from services.accounts import login as login_service
from services.accounts._login_state import _PENDING

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


def _fake_request(monkeypatch: pytest.MonkeyPatch, *, hash_value: str, error: str | None) -> None:
    async def _request(request: PhoneCodeRequest) -> PhoneCodeChallenge:
        return PhoneCodeChallenge(
            account_id=request.account_id,
            phone=request.phone,
            phone_code_hash=hash_value,
            error=error,
        )

    monkeypatch.setattr(login_service, "request_phone_code", _request)


def _fake_submit(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: SessionCheckStatus,
    error: str | None = None,
) -> None:
    async def _submit(request: PhoneCodeSubmit) -> TelegramSessionCheckResult:
        return TelegramSessionCheckResult(
            account_id=request.account_id,
            session_path="x",
            status=status,
            is_temporary=False,
            phone="79990001122",
            error_message=error,
        )

    monkeypatch.setattr(login_service, "submit_phone_code", _submit)


@pytest.mark.asyncio
async def test_start_phone_login_creates_account_with_phone() -> None:
    account = await login_service.start_phone_login("+7 999 000-11-22")

    assert account.account_id == "79990001122"
    assert account.session_name == "79990001122"
    assert account.phone == "+7 999 000-11-22"
    assert account.status == "new"


@pytest.mark.asyncio
async def test_start_phone_login_then_request_code_has_phone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = await login_service.start_phone_login("+79990001122")
    _fake_request(monkeypatch, hash_value="HASH", error=None)

    result = await login_service.request_login_code(account.account_id)

    assert result.phone == "+79990001122"
    assert _PENDING[account.account_id].phone_code_hash == "HASH"


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
    _fake_request(monkeypatch, hash_value="HASH", error=None)

    result = await login_service.request_login_code("acc")

    assert result.phone == "79990001122"
    assert _PENDING["acc"].phone_code_hash == "HASH"


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
    _fake_request(monkeypatch, hash_value="", error="flood wait 30s")

    with pytest.raises(login_service.PhoneLoginError, match="flood wait"):
        await login_service.request_login_code("acc")
    assert "acc" not in _PENDING


@pytest.mark.asyncio
async def test_submit_without_pending_code_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    await _account_with_phone("acc")
    _fake_submit(monkeypatch, status="alive")

    with pytest.raises(login_service.PhoneLoginError, match="request a new one"):
        await login_service.submit_login_code("acc", "11111")


@pytest.mark.asyncio
async def test_submit_login_code_signs_in_and_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    await _account_with_phone("acc")
    _fake_request(monkeypatch, hash_value="HASH", error=None)
    _fake_submit(monkeypatch, status="alive")
    await login_service.request_login_code("acc")

    account = await login_service.submit_login_code("acc", "11111")

    assert account.status == "alive"
    assert "acc" not in _PENDING


@pytest.mark.asyncio
async def test_submit_login_code_bad_code_keeps_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    await _account_with_phone("acc")
    _fake_request(monkeypatch, hash_value="HASH", error=None)
    _fake_submit(monkeypatch, status="unauthorized", error="phone code invalid")
    await login_service.request_login_code("acc")

    with pytest.raises(login_service.PhoneLoginError, match="phone code invalid"):
        await login_service.submit_login_code("acc", "00000")
    # the cached hash survives a wrong code so the operator can retry
    assert "acc" in _PENDING


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
