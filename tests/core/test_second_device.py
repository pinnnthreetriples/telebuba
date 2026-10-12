"""A second device signs in as its own authorization, or leaves nothing behind.

The new client asks Telegram for a code; the account's EXISTING session reads it from
777000, taking only a message newer than the id snapshotted before the request. A
failure revokes a sign-in that happened, removes the half-made ``.session`` and writes
no row, and the existing session is never touched.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from telethon import errors

from core.config import settings
from core.db import (
    _get_engine,
    configure_database,
    create_account,
    fetch_account,
    fetch_device_fingerprint,
    insert_device_fingerprint,
)
from core.device_fingerprint import generate_random_device_fingerprint
from core.repositories._accounts_twofa import set_account_twofa_password
from core.telegram_client import _pool
from core.telegram_client import _second_device as _second_device_module
from core.telegram_client._login_code import SERVICE_USER_ID, extract_login_code
from core.telegram_client._second_device import authorize_second_device
from schemas.accounts import AccountCreate
from schemas.devices import SecondDeviceSignIn

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemas.device_fingerprint import TelegramClientProfile

_PHONE = "+5215550001111"
_USER_ID = 4242


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    _get_engine()
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.telegram, "mobile_api_id", 6)
    monkeypatch.setattr(settings.telegram, "mobile_api_hash", "mobile-hash")
    monkeypatch.setattr(settings.telegram, "second_device_code_wait_seconds", 0.2)
    monkeypatch.setattr(settings.telegram, "second_device_code_poll_seconds", 0.01)
    _pool._reset_for_tests()
    yield
    _pool._reset_for_tests()


class _Primary:
    """The account's existing session: it sees the 777000 service chat."""

    def __init__(
        self,
        messages: list[tuple[int, str]],
        *,
        me: SimpleNamespace | None = None,
        unresolvable: bool = False,
    ) -> None:
        self.messages = list(messages)
        self.min_ids: list[int] = []
        # A sent code that Telegram delivers only after this many more reads.
        self.pending: str | None = None
        self.reads_until_delivery = 0
        # Who the session is: the phone and user id come from here, not the row.
        self.me = me if me is not None else SimpleNamespace(id=_USER_ID, phone=_PHONE[1:])
        # 777000 not in the session's entity cache, even after listing dialogs.
        self.unresolvable = unresolvable

    async def get_me(self) -> SimpleNamespace | None:
        return self.me

    async def get_dialogs(self, *, limit: int) -> list[object]:
        assert limit > 0
        return []

    async def get_messages(self, peer: int, *, limit: int, min_id: int) -> list[SimpleNamespace]:
        assert peer == SERVICE_USER_ID
        if self.unresolvable:
            msg = "Could not find the input entity for 777000"
            raise ValueError(msg)
        self.min_ids.append(min_id)
        if self.pending is not None:
            if self.reads_until_delivery == 0:
                newest = max((m for m, _ in self.messages), default=0)
                self.messages.append((newest + 1, self.pending))
                self.pending = None
            else:
                self.reads_until_delivery -= 1
        newest = sorted(self.messages, reverse=True)
        return [SimpleNamespace(id=mid, message=text) for mid, text in newest if mid > min_id][
            :limit
        ]


class _NewDevice:
    """The new authorization's client; ``on_code`` is Telegram delivering the code."""

    def __init__(self, profile: TelegramClientProfile, primary: _Primary, **behaviour) -> None:
        self.profile = profile
        self.primary = primary
        self.code_text = behaviour.get("code_text", "Login code: 54321. Do not give it to anyone")
        self.sign_in_error = behaviour.get("sign_in_error")
        self.needs_password = behaviour.get("needs_password", False)
        self.me_id = behaviour.get("me_id", _USER_ID)
        self.disconnect_error = behaviour.get("disconnect_error")
        # A code that reaches 777000 while the new client is still connecting.
        self.stale_on_connect = behaviour.get("stale_on_connect", False)
        self.calls: list[str] = []
        self.passwords: list[str] = []

    @property
    def session_file(self) -> Path:
        return Path(f"{self.profile.session_path}.session")

    async def connect(self) -> None:
        self.calls.append("connect")
        if self.stale_on_connect:
            newest = max((m for m, _ in self.primary.messages), default=0)
            self.primary.messages.append((newest + 1, "Login code: 99999"))
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.session_file.write_bytes(b"auth-key")

    async def send_code_request(self, phone: str) -> SimpleNamespace:
        self.calls.append(f"send_code:{phone}")
        if self.code_text is not None:
            self.primary.pending = self.code_text
        return SimpleNamespace(phone_code_hash="hash-1", type=SimpleNamespace(length=5))

    async def sign_in(self, *, phone=None, code=None, phone_code_hash=None, password=None):
        if password is not None:
            self.passwords.append(password)
            self.calls.append("sign_in:password")
            if self.sign_in_error is not None:
                raise self.sign_in_error
            return
        self.calls.append(f"sign_in:{code}:{phone_code_hash}:{phone}")
        if self.needs_password:
            raise errors.SessionPasswordNeededError(request=None)
        if self.sign_in_error is not None:
            raise self.sign_in_error
        return

    async def get_me(self) -> SimpleNamespace:
        return SimpleNamespace(id=self.me_id)

    async def log_out(self) -> None:
        self.calls.append("log_out")

    async def disconnect(self) -> None:
        self.calls.append("disconnect")
        if self.disconnect_error is not None:
            raise self.disconnect_error


async def _desktop_account() -> None:
    """An imported .session: no phone and no user id stored yet."""
    await create_account(AccountCreate(account_id="acc", session_name="acc"))
    await insert_device_fingerprint(generate_random_device_fingerprint("acc"))


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    primary: _Primary,
    **behaviour: object,
) -> list[_NewDevice]:
    made: list[_NewDevice] = []

    async def fake_get_client(account_id: str, device_type: str | None = None) -> _Primary:
        assert (account_id, device_type) == ("acc", None), "the code is read on the primary"
        return primary

    def fake_create(profile: TelegramClientProfile) -> _NewDevice:
        client = _NewDevice(profile, primary, **behaviour)
        made.append(client)
        return client

    monkeypatch.setattr("core.telegram_client._second_device.get_client", fake_get_client)
    monkeypatch.setattr("core.telegram_client._second_device.create_telegram_client", fake_create)
    return made


def _request() -> SecondDeviceSignIn:
    return SecondDeviceSignIn(
        account_id="acc",
        device_type="mobile",
        session_name="acc_mobile",
    )


async def _nothing_kept(client: _NewDevice) -> None:
    account = await fetch_account("acc")
    assert account is not None
    assert account.second_session_name is None
    assert account.devices == ["desktop"]
    assert await fetch_device_fingerprint("acc", "mobile") is None
    assert not client.session_file.exists(), "the half-made .session must be removed"


@pytest.mark.asyncio
async def test_a_successful_sign_in_records_the_device_and_its_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _desktop_account()
    desktop_before = await fetch_device_fingerprint("acc", "desktop")
    made = _wire(monkeypatch, _Primary([(10, "Login code: 11111")]))

    result = await authorize_second_device(_request())

    assert result.code is None
    client = made[0]
    assert client.calls == [
        "connect",
        f"send_code:{_PHONE}",
        f"sign_in:54321:hash-1:{_PHONE}",
        "disconnect",
    ]
    assert client.session_file.name == "acc_mobile.session"
    assert client.session_file.exists()
    account = await fetch_account("acc")
    assert account is not None
    assert (account.second_session_name, account.devices) == ("acc_mobile", ["desktop", "mobile"])
    mobile = await fetch_device_fingerprint("acc", "mobile")
    assert mobile is not None
    assert (mobile.platform, mobile.device_type) == ("android", "mobile")
    assert await fetch_device_fingerprint("acc", "desktop") == desktop_before


@pytest.mark.asyncio
async def test_the_code_is_read_only_after_the_snapshotted_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An older 777000 code (id 10) must never be replayed: the cut-off is the id, not a clock."""
    await _desktop_account()
    primary = _Primary([(9, "Login code: 99999"), (10, "Login code: 11111")])
    made = _wire(monkeypatch, primary)

    await authorize_second_device(_request())

    assert primary.min_ids[0] == 0, "the snapshot reads the newest id first"
    assert set(primary.min_ids[1:]) == {10}, "every later read starts after that id"
    assert f"sign_in:54321:hash-1:{_PHONE}" in made[0].calls


@pytest.mark.asyncio
async def test_no_new_code_times_out_and_keeps_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    await _desktop_account()
    made = _wire(monkeypatch, _Primary([(10, "Login code: 11111")]), code_text=None)

    result = await authorize_second_device(_request())

    assert result.code == "device_code_timeout"
    assert "log_out" not in made[0].calls, "nothing signed in, nothing to revoke"
    await _nothing_kept(made[0])


@pytest.mark.asyncio
async def test_a_rejected_code_is_refused_and_rolled_back(monkeypatch: pytest.MonkeyPatch) -> None:
    await _desktop_account()
    made = _wire(
        monkeypatch,
        _Primary([(1, "hi")]),
        sign_in_error=errors.PhoneCodeInvalidError(request=None),
    )

    result = await authorize_second_device(_request())

    assert result.code == "device_code_invalid"
    await _nothing_kept(made[0])


@pytest.mark.asyncio
async def test_two_factor_uses_the_stored_password(monkeypatch: pytest.MonkeyPatch) -> None:
    await _desktop_account()
    await set_account_twofa_password("acc", "cloud-pass")
    made = _wire(monkeypatch, _Primary([(1, "hi")]), needs_password=True)

    result = await authorize_second_device(_request())

    assert result.code is None
    assert made[0].passwords == ["cloud-pass"]


@pytest.mark.asyncio
async def test_two_factor_without_a_stored_password_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _desktop_account()
    made = _wire(monkeypatch, _Primary([(1, "hi")]), needs_password=True)

    result = await authorize_second_device(_request())

    assert result.code == "device_twofa_missing"
    await _nothing_kept(made[0])


@pytest.mark.asyncio
async def test_a_wrong_stored_password_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    await _desktop_account()
    await set_account_twofa_password("acc", "stale")
    made = _wire(
        monkeypatch,
        _Primary([(1, "hi")]),
        needs_password=True,
        sign_in_error=errors.PasswordHashInvalidError(request=None),
    )

    result = await authorize_second_device(_request())

    assert result.code == "device_twofa_invalid"
    await _nothing_kept(made[0])


@pytest.mark.asyncio
async def test_a_sign_in_on_another_user_is_logged_out_and_discarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _desktop_account()
    made = _wire(monkeypatch, _Primary([(1, "hi")]), me_id=999)

    result = await authorize_second_device(_request())

    assert result.code == "device_user_mismatch"
    assert made[0].calls[-2:] == ["log_out", "disconnect"], "a sign-in that happened is revoked"
    await _nothing_kept(made[0])


@pytest.mark.asyncio
async def test_a_flood_wait_carries_its_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    await _desktop_account()
    flood = errors.FloodWaitError(request=None, capture=120)
    made = _wire(monkeypatch, _Primary([(1, "hi")]), sign_in_error=flood)

    result = await authorize_second_device(_request())

    assert (result.code, result.flood_wait_seconds) == ("device_flood_wait", 120)
    await _nothing_kept(made[0])


@pytest.mark.asyncio
async def test_an_existing_file_at_the_name_is_never_signed_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _desktop_account()
    made = _wire(monkeypatch, _Primary([(1, "hi")]))
    stray = Path(settings.telegram.session_dir) / "acc_mobile.session"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"someone else's key")

    result = await authorize_second_device(_request())

    assert result.code == "device_session_conflict"
    assert made == []
    assert stray.read_bytes() == b"someone else's key"


@pytest.mark.asyncio
async def test_mobile_needs_its_own_app_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    await _desktop_account()
    monkeypatch.setattr(settings.telegram, "mobile_api_hash", "")
    made = _wire(monkeypatch, _Primary([(1, "hi")]))

    result = await authorize_second_device(_request())

    assert result.code == "mobile_api_not_configured"
    assert made == []


@pytest.mark.asyncio
async def test_a_device_the_account_already_has_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    await _desktop_account()
    made = _wire(monkeypatch, _Primary([(1, "hi")]))

    result = await authorize_second_device(_request().model_copy(update={"device_type": "desktop"}))

    assert result.code == "device_already_present"
    assert made == []


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Login code: 54321. Do not give this code to anyone", "54321"),
        ("Код для входа в Telegram: 12345", "12345"),
        ("Your code 123456 expires", None),
        (None, None),
    ],
)
def test_the_code_is_the_labelled_number_of_the_expected_length(
    text: str | None,
    code: str | None,
) -> None:
    assert extract_login_code(text, 5) == code


@pytest.mark.asyncio
async def test_a_session_that_knows_no_phone_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    await _desktop_account()
    made = _wire(monkeypatch, _Primary([(1, "hi")], me=SimpleNamespace(id=_USER_ID, phone=None)))

    result = await authorize_second_device(_request())

    assert result.code == "device_phone_unknown"
    assert made == []


@pytest.mark.asyncio
async def test_a_signed_out_primary_has_nothing_to_read_the_code_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _desktop_account()
    primary = _Primary([(1, "hi")])
    primary.me = None  # ty: ignore[invalid-assignment]
    made = _wire(monkeypatch, primary)

    result = await authorize_second_device(_request())

    assert result.code == "device_primary_unavailable"
    assert made == []


@pytest.mark.asyncio
async def test_no_777000_chat_yet_means_every_later_message_is_new(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unresolvable service chat is cut-off 0, not a raw ValueError for the operator."""
    await _desktop_account()
    made = _wire(monkeypatch, _Primary([], unresolvable=True), code_text=None)

    result = await authorize_second_device(_request())

    assert result.code == "device_code_timeout"
    await _nothing_kept(made[0])


@pytest.mark.asyncio
async def test_a_failing_disconnect_still_removes_the_half_made_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _desktop_account()
    made = _wire(
        monkeypatch,
        _Primary([(1, "hi")]),
        sign_in_error=errors.PhoneCodeInvalidError(request=None),
        disconnect_error=ConnectionError("socket gone"),
    )

    result = await authorize_second_device(_request())

    assert result.code == "device_code_invalid"
    await _nothing_kept(made[0])


@pytest.mark.asyncio
async def test_a_vanished_account_has_no_session_to_read_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made = _wire(monkeypatch, _Primary([(1, "hi")]))

    result = await authorize_second_device(_request())

    assert result.code == "device_primary_unavailable"
    assert made == []


@pytest.mark.asyncio
async def test_a_code_that_arrived_before_the_request_is_never_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cut-off is taken right before the code request, after the client connected."""
    await _desktop_account()
    primary = _Primary([(10, "hi")])
    primary.reads_until_delivery = 1  # the stale code is the newest at the first read
    made = _wire(monkeypatch, primary, stale_on_connect=True)

    result = await authorize_second_device(_request())

    assert result.code is None
    assert f"sign_in:54321:hash-1:{_PHONE}" in made[0].calls


@pytest.mark.asyncio
async def test_a_cancellation_after_the_record_keeps_the_recorded_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The commit cannot be stopped; what it did decides what is undone."""
    await _desktop_account()
    made = _wire(monkeypatch, _Primary([(1, "hi")]))
    entered, release = asyncio.Event(), asyncio.Event()
    real = _second_device_module.record_second_device

    async def slow_record(name: str, fingerprint: object) -> None:
        entered.set()
        await release.wait()
        await real(name, fingerprint)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(_second_device_module, "record_second_device", slow_record)
    task = asyncio.create_task(authorize_second_device(_request()))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    account = await fetch_account("acc")
    assert account is not None
    assert account.devices == ["desktop", "mobile"]
    assert made[0].session_file.exists(), "a recorded device keeps its session"
    assert "log_out" not in made[0].calls
