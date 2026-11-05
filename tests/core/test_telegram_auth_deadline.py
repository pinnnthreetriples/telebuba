"""Every phone-login RPC ends: a dead proxy must not hold the account's auth lock forever."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.telegram_client import _auth as auth_module
from core.telegram_client import log_out_session, request_phone_code, submit_phone_code
from schemas.device_fingerprint import TelegramClientRequest
from schemas.phone_login import PhoneCodeRequest, PhoneCodeSubmit
from tests.core.test_telegram_auth import FakeAuthClient, _patch_client

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _fast_deadline(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings.telegram, "session_check_timeout_seconds", 0.05)
    monkeypatch.setattr(settings.telegram, "timeout_seconds", 0.05)
    monkeypatch.setattr(settings.telegram, "signin_probe_timeout_seconds", 0.05)
    auth_module._AUTH_LOCKS.clear()
    yield
    auth_module._AUTH_LOCKS.clear()


class HangingClient(FakeAuthClient):
    """Telethon's ``_call`` awaits the response with no timeout: the RPC never returns."""

    def __init__(
        self, *, hang: str, hang_disconnect: bool = False, authorized: bool = False
    ) -> None:
        super().__init__()
        self.hang = hang
        self.authorized = authorized
        self.hang_disconnect = hang_disconnect

    async def connect(self) -> None:
        if self.hang == "connect":
            await asyncio.Event().wait()

    async def send_code_request(self, phone: str):
        if self.hang == "send":
            await asyncio.Event().wait()
        return await super().send_code_request(phone)

    async def sign_in(self, **kwargs: object):
        if self.hang == "sign_in":
            await asyncio.Event().wait()
        return await super().sign_in(**kwargs)

    async def log_out(self) -> bool:
        if self.hang == "log_out":
            await asyncio.Event().wait()
        return await super().log_out()

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def disconnect(self) -> None:
        await super().disconnect()
        if self.hang_disconnect:
            await asyncio.Event().wait()


_REQUEST = PhoneCodeRequest(account_id="acc", phone="79990001122")
_SUBMIT = PhoneCodeSubmit(
    account_id="acc",
    phone="79990001122",
    phone_code_hash="H",
    code="11111",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("hang", ["connect", "send"])
async def test_request_code_that_never_answers_ends_as_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hang: str,
) -> None:
    configure_database(tmp_path / "telebuba.db")
    client = HangingClient(hang=hang)
    _patch_client(monkeypatch, tmp_path, client)

    challenge = await asyncio.wait_for(request_phone_code(_REQUEST), timeout=5)

    assert (challenge.phone_code_hash, challenge.error_code) == ("", "unavailable")
    assert challenge.error == "TimeoutError"
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_a_hung_request_does_not_block_the_next_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_database(tmp_path / "telebuba.db")
    hung = HangingClient(hang="send")
    _patch_client(monkeypatch, tmp_path, hung)
    await asyncio.wait_for(request_phone_code(_REQUEST), timeout=5)

    healthy = FakeAuthClient()
    monkeypatch.setattr("core.telegram_client._auth.create_telegram_client", lambda _: healthy)
    challenge = await asyncio.wait_for(request_phone_code(_REQUEST), timeout=5)

    assert challenge.phone_code_hash == "HASH-123"


@pytest.mark.asyncio
async def test_a_stuck_disconnect_cannot_hold_the_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_database(tmp_path / "telebuba.db")
    client = HangingClient(hang="none", hang_disconnect=True)
    _patch_client(monkeypatch, tmp_path, client)

    challenge = await asyncio.wait_for(request_phone_code(_REQUEST), timeout=5)

    assert challenge.phone_code_hash == "HASH-123"
    assert not auth_module._auth_lock("acc").locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("hang", ["connect", "sign_in"])
async def test_submit_that_never_answers_ends_as_a_temporary_network_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hang: str,
) -> None:
    configure_database(tmp_path / "telebuba.db")
    client = HangingClient(hang=hang)
    _patch_client(monkeypatch, tmp_path, client)

    result = await asyncio.wait_for(submit_phone_code(_SUBMIT), timeout=5)

    assert (result.status, result.is_temporary, result.error_type) == (
        "network_error",
        True,
        "TimeoutError",
    )
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_logout_that_never_answers_still_ends_unauthorized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_database(tmp_path / "telebuba.db")
    client = HangingClient(hang="log_out")
    _patch_client(monkeypatch, tmp_path, client)

    result = await asyncio.wait_for(
        log_out_session(TelegramClientRequest(account_id="acc"), wipe_session=False),
        timeout=5,
    )

    assert result.status == "unauthorized"
    assert result.error_message == "TimeoutError"
    assert client.disconnected is True
    assert not auth_module._auth_lock("acc").locked()


class _AcceptedThenSlow(HangingClient):
    """Telegram accepted ``sign_in``; the answer to ``get_me`` outruns the deadline."""

    def __init__(self) -> None:
        super().__init__(hang="none", authorized=True)
        self.slow = True

    async def get_me(self):
        if self.slow:
            self.slow = False
            await asyncio.Event().wait()
        return await super().get_me()


@pytest.mark.asyncio
async def test_a_deadline_after_the_server_accepted_the_sign_in_is_still_alive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_database(tmp_path / "telebuba.db")
    _patch_client(monkeypatch, tmp_path, _AcceptedThenSlow())

    result = await asyncio.wait_for(submit_phone_code(_SUBMIT), timeout=5)

    assert (result.status, result.typed_password_accepted) == ("alive", False)


@pytest.mark.asyncio
async def test_a_deadline_with_no_authorization_stays_a_network_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_database(tmp_path / "telebuba.db")
    _patch_client(monkeypatch, tmp_path, HangingClient(hang="sign_in", authorized=False))

    result = await asyncio.wait_for(submit_phone_code(_SUBMIT), timeout=5)

    assert result.status == "network_error"


class _PasswordLandsThenSlow(HangingClient):
    """2FA account: the code asks for a password, ``reject`` ones are refused, the next hangs.

    The hang is after Telegram applied the sign-in: ``authorized`` says so to the probe.
    """

    def __init__(self, *, reject: tuple[str, ...]) -> None:
        super().__init__(hang="none", authorized=True)
        self.needs_2fa = True
        self.reject = reject

    async def sign_in(self, **kwargs: object):
        password = kwargs.get("password")
        if password in self.reject:
            raise auth_module.errors.PasswordHashInvalidError(request=None)
        if password is not None:
            await asyncio.Event().wait()
        return await super().sign_in(**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reject", "expected"),
    [
        pytest.param((), True, id="only-the-typed-one-was-tried"),
        pytest.param(("typed",), False, id="a-fallback-was-being-tried"),
    ],
)
async def test_a_deadline_during_the_password_step_reports_which_password_was_in_flight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reject: tuple[str, ...],
    expected: bool,  # noqa: FBT001
) -> None:
    configure_database(tmp_path / "telebuba.db")
    _patch_client(monkeypatch, tmp_path, _PasswordLandsThenSlow(reject=reject))
    submit = _SUBMIT.model_copy(update={"password": "typed", "fallback_passwords": ["fallback"]})

    result = await asyncio.wait_for(submit_phone_code(submit), timeout=5)

    assert (result.status, result.typed_password_accepted) == ("alive", expected)
