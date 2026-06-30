"""Gateway tests for the phone-code login + logout RPCs (Telethon faked)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from telethon import errors

from core.db import configure_database
from core.telegram_client import log_out_session, request_phone_code, submit_phone_code
from schemas.device_fingerprint import TelegramClientRequest
from schemas.phone_login import PhoneCodeRequest, PhoneCodeSubmit

if TYPE_CHECKING:
    from pathlib import Path


class FakeUser:
    id = 555
    phone = "79990001122"
    username = "logged_in"
    first_name = "Code"
    last_name = "Login"


class FakeSent:
    phone_code_hash = "HASH-123"


class FakeAuthClient:
    def __init__(
        self,
        *,
        needs_2fa: bool = False,
        sign_in_error: Exception | None = None,
        send_error: Exception | None = None,
    ) -> None:
        self.needs_2fa = needs_2fa
        self.sign_in_error = sign_in_error
        self.send_error = send_error
        self.disconnected = False
        self.logged_out = False
        self.password_used = False

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        self.disconnected = True

    async def send_code_request(self, phone: str) -> FakeSent:  # noqa: ARG002
        if self.send_error is not None:
            raise self.send_error
        return FakeSent()

    async def sign_in(self, **kwargs: object) -> FakeUser:
        if "password" in kwargs:
            self.password_used = True
            return FakeUser()
        if self.sign_in_error is not None:
            raise self.sign_in_error
        if self.needs_2fa:
            raise errors.SessionPasswordNeededError(request=None)
        return FakeUser()

    async def get_me(self) -> FakeUser:
        return FakeUser()

    async def log_out(self) -> bool:
        self.logged_out = True
        return True


def _patch_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, client: FakeAuthClient) -> None:
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")
    monkeypatch.setattr("core.config.settings.telegram.api_id", 12345)
    monkeypatch.setattr("core.config.settings.telegram.api_hash", "hash")
    monkeypatch.setattr("core.telegram_client._auth.create_telegram_client", lambda _: client)


@pytest.mark.asyncio
async def test_request_phone_code_returns_the_hash(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient()
    _patch_client(monkeypatch, tmp_path, client)

    challenge = await request_phone_code(PhoneCodeRequest(account_id="acc", phone="79990001122"))

    assert challenge.phone_code_hash == "HASH-123"
    assert challenge.error is None
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_request_phone_code_classifies_failure(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient(send_error=errors.FloodWaitError(request=None, capture=30))
    _patch_client(monkeypatch, tmp_path, client)

    challenge = await request_phone_code(PhoneCodeRequest(account_id="acc", phone="79990001122"))

    assert challenge.phone_code_hash == ""
    assert "flood wait" in (challenge.error or "")


@pytest.mark.asyncio
async def test_submit_phone_code_signs_in(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient()
    _patch_client(monkeypatch, tmp_path, client)

    result = await submit_phone_code(
        PhoneCodeSubmit(account_id="acc", phone="79990001122", phone_code_hash="H", code="11111"),
    )

    assert result.status == "alive"
    assert result.user_id == 555
    assert result.username == "logged_in"
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_submit_phone_code_handles_2fa(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient(needs_2fa=True)
    _patch_client(monkeypatch, tmp_path, client)

    result = await submit_phone_code(
        PhoneCodeSubmit(
            account_id="acc",
            phone="79990001122",
            phone_code_hash="H",
            code="11111",
            password="hunter2",
        ),
    )

    assert result.status == "alive"
    assert client.password_used is True


@pytest.mark.asyncio
async def test_submit_phone_code_2fa_required_without_password(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient(needs_2fa=True)
    _patch_client(monkeypatch, tmp_path, client)

    result = await submit_phone_code(
        PhoneCodeSubmit(account_id="acc", phone="79990001122", phone_code_hash="H", code="11111"),
    )

    assert result.status == "unauthorized"
    assert result.error_type == "SessionPasswordNeededError"


@pytest.mark.asyncio
async def test_submit_phone_code_invalid_code(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient(sign_in_error=errors.PhoneCodeInvalidError(request=None))
    _patch_client(monkeypatch, tmp_path, client)

    result = await submit_phone_code(
        PhoneCodeSubmit(account_id="acc", phone="79990001122", phone_code_hash="H", code="00000"),
    )

    assert result.status == "unauthorized"
    assert result.error_type == "PhoneCodeInvalidError"


@pytest.mark.asyncio
async def test_log_out_session_marks_unauthorized(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient()
    _patch_client(monkeypatch, tmp_path, client)

    result = await log_out_session(TelegramClientRequest(account_id="acc", receive_updates=False))

    assert result.status == "unauthorized"
    assert client.logged_out is True
    assert client.disconnected is True
