"""Gateway tests for the QR-code sign-in (Telethon and its ``QRLogin`` faked)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from telethon import errors

from core.config import settings
from core.db import configure_database
from core.telegram_client import (
    QrLoginGatewayError,
    cancel_qr_login,
    forget_qr_login,
    read_qr_login,
    shutdown_qr_logins,
    start_qr_login,
    submit_qr_password,
)
from core.telegram_client import _auth as auth_module
from core.telegram_client import _qr_login as qr_module
from core.telegram_client._pool import _is_removing
from schemas.qr_login import QrLoginRequest, QrLoginState

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

_TOKEN_MARKER = "SECRET-TOKEN"
_PROXY_TEXT = "Could not connect to proxy 203.0.113.9:1080 [Connection refused]"


class FakeUser:
    id = 777
    phone = "79990001122"
    username = "scanned"
    first_name = "Qr"
    last_name = "Login"


class FakeQr:
    """A scripted ``QRLogin``: each ``wait`` pops one step."""

    def __init__(self, script: list[object]) -> None:
        self.script = list(script)
        self.recreated = 0
        self.timeouts: list[float] = []
        self._rotate()

    def _rotate(self) -> None:
        self.url = f"tg://login?token={_TOKEN_MARKER}-{self.recreated}"
        self.expires = datetime.now(UTC) + timedelta(seconds=30)

    async def wait(self, timeout: float) -> FakeUser:  # noqa: ASYNC109 - mirrors Telethon
        self.timeouts.append(timeout)
        step = self.script.pop(0) if self.script else "hang"
        if step == "hang":
            await asyncio.sleep(3600)
        if step == "slow-timeout":
            await asyncio.sleep(0.05)
            raise TimeoutError
        if step == "timeout":
            raise TimeoutError
        if isinstance(step, BaseException):
            raise step
        return FakeUser()

    async def recreate(self) -> None:
        self.recreated += 1
        self._rotate()


class FakeClient:
    def __init__(
        self,
        qr: FakeQr | None = None,
        *,
        authorized: bool = False,
        connect_error: Exception | None = None,
        connect_hangs: bool = False,
        right_password: str = "right",
    ) -> None:
        self.qr = qr or FakeQr(["hang"])
        self.authorized = authorized
        self.connect_error = connect_error
        self.connect_hangs = connect_hangs
        self.right_password = right_password
        self.disconnected = False
        self.ignored_ids: list[int] | None = None
        self.passwords: list[str] = []

    async def connect(self) -> None:
        if self.connect_hangs:
            await asyncio.sleep(3600)
        if self.connect_error is not None:
            raise self.connect_error

    async def disconnect(self) -> None:
        self.disconnected = True

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def get_me(self) -> FakeUser:
        return FakeUser()

    async def qr_login(self, ignored_ids: list[int]) -> FakeQr:
        self.ignored_ids = ignored_ids
        return self.qr

    async def sign_in(self, *, password: str) -> FakeUser:
        self.passwords.append(password)
        if password != self.right_password:
            raise errors.PasswordHashInvalidError(request=None)
        return FakeUser()


@pytest_asyncio.fixture(autouse=True)
async def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.telegram, "api_id", 12345)
    monkeypatch.setattr(settings.telegram, "api_hash", "hash")
    # Pydantic does not validate assignment, so the ``ge=30`` floor does not apply.
    monkeypatch.setattr(settings.telegram, "timeout_seconds", 2)
    monkeypatch.setattr(settings.telegram, "qr_login_timeout_seconds", 30)
    auth_module._AUTH_LOCKS.clear()
    qr_module._SESSIONS.clear()
    yield
    await shutdown_qr_logins()
    auth_module._AUTH_LOCKS.clear()
    qr_module._SESSIONS.clear()


def _use(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> list[object]:
    """Make the gateway build ``client``; the list collects the profiles it was built for."""
    profiles: list[object] = []

    def _create(profile: object) -> FakeClient:
        profiles.append(profile)
        return client

    monkeypatch.setattr(qr_module, "create_telegram_client", _create)
    return profiles


async def _until(ready: Callable[[], bool], *, seconds: float = 3.0) -> None:
    async with asyncio.timeout(seconds):
        while not ready():  # noqa: ASYNC110 - polling plain state, no event to await
            await asyncio.sleep(0.01)


def _state(account_id: str = "acc") -> QrLoginState:
    state = read_qr_login(account_id)
    assert state is not None
    return state


def _request(**overrides: object) -> QrLoginRequest:
    return QrLoginRequest.model_validate({"account_id": "acc", **overrides})


@pytest.mark.asyncio
async def test_start_shows_a_qr_with_updates_on_and_ignored_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    profiles = _use(monkeypatch, client)

    state = await start_qr_login(_request(ignored_user_ids=[5, 6]))

    assert state.status == "waiting"
    assert state.qr_image is not None
    assert state.qr_image.startswith("data:image/svg+xml")
    assert state.expires_at == client.qr.expires
    assert client.ignored_ids == [5, 6]
    assert [getattr(profile, "receive_updates", None) for profile in profiles] == [True]
    assert read_qr_login("acc") == state


@pytest.mark.asyncio
async def test_the_token_never_reaches_the_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, FakeClient())

    state = await start_qr_login(_request())

    assert _TOKEN_MARKER not in state.model_dump_json()
    assert "tg://" not in state.model_dump_json()


@pytest.mark.asyncio
async def test_a_naive_expiry_is_read_as_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    qr = FakeQr(["hang"])
    qr.expires = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=30)
    _use(monkeypatch, FakeClient(qr))

    state = await start_qr_login(_request())

    assert state.expires_at is not None
    assert state.expires_at.tzinfo is not None


@pytest.mark.asyncio
async def test_a_lapsed_token_is_replaced_with_a_new_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qr = FakeQr(["timeout", "hang"])
    _use(monkeypatch, FakeClient(qr))

    await start_qr_login(_request())
    await _until(lambda: qr.recreated == 1 and len(qr.timeouts) == 2)

    assert _state().status == "waiting"
    assert _state().qr_image is not None
    assert qr.timeouts[0] <= 30
    assert qr.timeouts[0] >= 1


@pytest.mark.asyncio
async def test_a_new_token_changes_the_image(monkeypatch: pytest.MonkeyPatch) -> None:
    qr = FakeQr(["slow-timeout", "hang"])
    _use(monkeypatch, FakeClient(qr))

    first = await start_qr_login(_request())
    await _until(lambda: qr.recreated == 1 and _state().qr_image != first.qr_image)

    assert _state().qr_image != first.qr_image


@pytest.mark.asyncio
async def test_an_accepted_scan_ends_alive_with_the_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(FakeQr(["accepted"]))
    _use(monkeypatch, client)

    await start_qr_login(_request())
    await _until(lambda: _state().status == "alive")

    result = _state().result
    assert result is not None
    assert result.status == "alive"
    assert result.user_id == 777
    assert result.username == "scanned"
    assert result.phone == "79990001122"
    await _until(lambda: client.disconnected)


@pytest.mark.asyncio
async def test_an_authorized_session_is_alive_without_a_qr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(authorized=True)
    _use(monkeypatch, client)

    state = await start_qr_login(_request())

    assert state.status == "alive"
    assert state.qr_image is None
    assert state.result is not None
    assert state.result.user_id == 777
    assert client.ignored_ids is None


@pytest.mark.asyncio
async def test_two_factor_asks_for_the_password_until_it_is_right(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(FakeQr([errors.SessionPasswordNeededError(request=None)]))
    _use(monkeypatch, client)

    await start_qr_login(_request())
    await _until(lambda: _state().status == "password_needed")
    assert _state().error_code is None
    assert _state().qr_image is None

    wrong = await submit_qr_password("acc", "wrong")
    assert wrong.status == "password_needed"
    assert wrong.error_code == "password_invalid"

    right = await submit_qr_password("acc", "right")
    assert right.status == "alive"
    assert right.result is not None
    assert right.result.user_id == 777
    assert client.passwords == ["wrong", "right"]
    assert "right" not in right.model_dump_json()


@pytest.mark.asyncio
async def test_the_deadline_expires_a_forgotten_qr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.telegram, "qr_login_timeout_seconds", 0.2)
    client = FakeClient(FakeQr(["slow-timeout"] * 50))
    _use(monkeypatch, client)

    await start_qr_login(_request())
    await _until(lambda: _state().status == "expired")

    await _until(lambda: client.disconnected)
    assert _state().qr_image is None


@pytest.mark.asyncio
async def test_the_deadline_also_ends_a_password_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.telegram, "qr_login_timeout_seconds", 0.2)
    client = FakeClient(FakeQr([errors.SessionPasswordNeededError(request=None)]))
    _use(monkeypatch, client)

    await start_qr_login(_request())
    await _until(lambda: _state().status == "expired")

    await _until(lambda: client.disconnected)


@pytest.mark.asyncio
async def test_an_expired_deadline_before_the_first_qr_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.telegram, "qr_login_timeout_seconds", -1)
    _use(monkeypatch, FakeClient())

    state = await start_qr_login(_request())

    assert state.status == "expired"


@pytest.mark.asyncio
async def test_a_flood_wait_fails_with_its_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient(FakeQr([errors.FloodWaitError(request=None, capture=42)]))
    _use(monkeypatch, client)

    await start_qr_login(_request())
    await _until(lambda: _state().status == "failed")

    assert _state().error_code == "flood_wait"
    assert _state().flood_wait_seconds == 42
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_any_other_failure_is_classified_without_its_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(connect_error=ConnectionError(_PROXY_TEXT))
    _use(monkeypatch, client)

    state = await start_qr_login(_request())

    assert state.status == "failed"
    assert state.error_code == "connection_failed"
    assert "203.0.113.9" not in state.model_dump_json()
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_the_failure_log_carries_only_the_class_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str, dict[str, object] | None]] = []

    async def _log(
        level: str,
        event: str,
        account_id: str | None = None,  # noqa: ARG001
        extra: dict[str, object] | None = None,
    ) -> None:
        events.append((level, event, extra))

    monkeypatch.setattr(qr_module, "log_event", _log)
    _use(monkeypatch, FakeClient(connect_error=ConnectionError(_PROXY_TEXT)))

    await start_qr_login(_request())

    assert events == [("WARNING", "qr_login_failed", {"error_type": "ConnectionError"})]


@pytest.mark.asyncio
async def test_start_gives_up_when_no_qr_appears_in_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.telegram, "timeout_seconds", 0.1)
    client = FakeClient(connect_hangs=True)
    _use(monkeypatch, client)

    state = await start_qr_login(_request())

    assert state.status == "failed"
    assert state.error_code == "connection_failed"
    assert read_qr_login("acc") is None
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_cancel_disconnects_the_client_and_forgets_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    _use(monkeypatch, client)
    await start_qr_login(_request())

    await cancel_qr_login("acc")

    assert client.disconnected is True
    assert read_qr_login("acc") is None
    assert not auth_module._auth_lock("acc").locked()
    assert not _is_removing("acc")


@pytest.mark.asyncio
async def test_cancel_without_a_session_is_a_no_op() -> None:
    await cancel_qr_login("nobody")

    assert read_qr_login("nobody") is None


@pytest.mark.asyncio
async def test_a_restart_cancels_the_previous_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    first = FakeClient()
    second = FakeClient(FakeQr(["hang"]))
    clients = iter([first, second])
    monkeypatch.setattr(qr_module, "create_telegram_client", lambda _: next(clients))

    await start_qr_login(_request())
    state = await start_qr_login(_request())

    assert first.disconnected is True
    assert second.disconnected is False
    assert state.status == "waiting"
    assert read_qr_login("acc") == state


@pytest.mark.asyncio
async def test_forget_drops_a_finished_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, FakeClient(authorized=True))
    await start_qr_login(_request())

    forget_qr_login("acc")

    assert read_qr_login("acc") is None


@pytest.mark.asyncio
async def test_shutdown_cancels_every_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    clients = {"a": FakeClient(), "b": FakeClient()}
    order: list[str] = []

    def _create(profile: object) -> FakeClient:
        account_id = str(getattr(profile, "account_id", ""))
        order.append(account_id)
        return clients[account_id]

    monkeypatch.setattr(qr_module, "create_telegram_client", _create)
    await start_qr_login(_request(account_id="a"))
    await start_qr_login(_request(account_id="b"))

    await shutdown_qr_logins()

    assert all(client.disconnected for client in clients.values())
    assert read_qr_login("a") is None
    assert read_qr_login("b") is None


@pytest.mark.asyncio
async def test_submit_without_a_session_is_refused() -> None:
    with pytest.raises(QrLoginGatewayError) as excinfo:
        await submit_qr_password("acc", "pw")

    assert excinfo.value.code == "no_qr_login"


@pytest.mark.asyncio
async def test_submit_while_waiting_for_a_scan_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use(monkeypatch, FakeClient())
    await start_qr_login(_request())

    with pytest.raises(QrLoginGatewayError) as excinfo:
        await submit_qr_password("acc", "pw")

    assert excinfo.value.code == "not_awaiting_password"


@pytest.mark.asyncio
async def test_a_password_nobody_answers_ends_as_connection_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(FakeQr([errors.SessionPasswordNeededError(request=None)]))
    _use(monkeypatch, client)
    await start_qr_login(_request())
    await _until(lambda: _state().status == "password_needed")
    # The sign-in task is gone, so nothing will ever take the password.
    session = qr_module._SESSIONS["acc"]
    assert session.task is not None
    session.task.cancel()
    await asyncio.gather(session.task, return_exceptions=True)
    monkeypatch.setattr(settings.telegram, "timeout_seconds", 0.1)

    state = await submit_qr_password("acc", "pw")

    assert state.status == "failed"
    assert state.error_code == "connection_failed"
