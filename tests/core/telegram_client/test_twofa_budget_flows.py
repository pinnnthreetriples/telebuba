"""The bound on wrong cloud passwords, at every place a password is sent to Telegram."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from telethon import errors

from core import twofa_budget
from core.config import settings
from core.db import create_account, set_account_twofa_password, set_account_twofa_pending
from core.repositories._accounts_twofa import (
    mark_account_twofa_pending_refused,
    remaining_twofa_attempts,
    twofa_password_candidates,
)
from core.telegram_client import _auth, _qr_login
from core.telegram_client._passkey import PasskeyGatewayError
from core.telegram_client._passkey_login import _second_factor
from core.telegram_client._rekey_errors import _classify as classify_rekey
from core.telegram_client._second_device import _classify as classify_device
from core.telegram_client._twofa_candidates import sign_in_with_passwords
from core.twofa_budget import TwofaAttemptsExhaustedError
from schemas.accounts import AccountCreate
from schemas.devices import SecondDeviceSignIn
from schemas.phone_login import PhoneCodeSubmit
from schemas.qr_login import QrLoginState

_MAX = 3
_SECRETS = ("old-secret", "seller-secret", "typed-secret", "wrong")


class _Client:
    """Telegram refusing every password except ``good``; records what was sent."""

    def __init__(self, good: str | None = None) -> None:
        self.good = good
        self.sent: list[str] = []

    async def sign_in(self, *, password: str | None = None, **_code: object) -> None:
        if password is None:
            raise errors.SessionPasswordNeededError(request=None)
        self.sent.append(password)
        if password != self.good:
            raise errors.PasswordHashInvalidError(request=None)


Logged = list[tuple[str, dict[str, Any]]]


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> Logged:
    rows: Logged = []

    async def _log(_level: str, event: str, **kwargs: Any) -> None:
        rows.append((event, kwargs))

    monkeypatch.setattr(twofa_budget, "log_event", _log)
    monkeypatch.setattr(settings.session_audit, "wrong_answer_limit", _MAX)
    return rows


async def _left() -> int:
    return await remaining_twofa_attempts("a", max_rejections=_MAX, window_hours=24.0)


async def _stored(*, pending: str | None = None) -> None:
    await create_account(AccountCreate(account_id="a"))
    await set_account_twofa_password("a", "old-secret")
    if pending:
        await set_account_twofa_pending("a", pending)


async def _send(client: _Client, passwords: list[str], *, typed: str | None = None) -> str:
    return await sign_in_with_passwords(client, "a", passwords, typed=typed)  # ty: ignore[invalid-argument-type]


async def _rejected(client: _Client, passwords: list[str], *, typed: str | None = None) -> None:
    with pytest.raises(errors.PasswordHashInvalidError):
        await _send(client, passwords, typed=typed)


async def _refused(client: _Client, passwords: list[str], *, typed: str | None = None) -> None:
    with pytest.raises(TwofaAttemptsExhaustedError):
        await _send(client, passwords, typed=typed)


@pytest.mark.asyncio
@pytest.mark.usefixtures("logged")
async def test_stored_passwords_stop_being_sent_once_the_budget_is_spent() -> None:
    await _stored(pending="seller-secret")
    client = _Client()

    # Two separate sign-in attempts (resend the code, try again). The budget is checked once
    # per attempt: the second starts with one unit left and so tries both stored passwords.
    await _rejected(client, ["old-secret", "seller-secret"])
    await _rejected(client, ["old-secret", "seller-secret"])
    assert client.sent == ["old-secret", "seller-secret", "old-secret", "seller-secret"]

    # The next attempt sends nothing at all.
    await _refused(client, ["old-secret"])
    assert client.sent == ["old-secret", "seller-secret", "old-secret", "seller-secret"]
    assert await _left() == 0


@pytest.mark.asyncio
async def test_the_exhaustion_is_journalled_once_and_never_with_a_password(
    logged: Logged,
) -> None:
    await _stored(pending="seller-secret")
    client = _Client()
    await _rejected(client, ["old-secret", "seller-secret"])
    await _rejected(client, ["old-secret", "seller-secret"])
    for _ in range(3):  # blocked attempts say nothing more
        await _refused(client, ["old-secret"])
    # ... and a typed password rejected beyond the limit is counted, not announced again.
    await _rejected(client, ["typed-secret"], typed="typed-secret")

    assert logged == [
        (
            "twofa_attempts_exhausted",
            {"account_id": "a", "extra": {"window_hours": 24.0, "count": _MAX}},
        )
    ]
    assert all(secret not in repr(logged) for secret in _SECRETS)


@pytest.mark.asyncio
@pytest.mark.usefixtures("logged")
async def test_a_password_the_operator_typed_is_sent_when_spent_and_still_counted() -> None:
    await _stored()
    client = _Client()
    for _ in range(_MAX):
        await _rejected(client, ["old-secret"])
    assert await _left() == 0
    client.sent.clear()

    await _rejected(client, ["typed-secret"], typed="typed-secret")

    assert client.sent == ["typed-secret"]
    assert await _left() == 0
    # A stored one beside it is still refused before it is sent.
    await _refused(client, ["typed-secret", "old-secret"], typed="typed-secret")
    assert client.sent == ["typed-secret", "typed-secret"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("logged")
async def test_an_accepted_password_gives_the_budget_back() -> None:
    await _stored()
    for _ in range(_MAX - 1):
        await _rejected(_Client(), ["wrong"], typed="wrong")
    assert await _left() == 1

    assert await _send(_Client("old-secret"), ["old-secret"]) == "old-secret"

    assert await _left() == _MAX


@pytest.mark.asyncio
@pytest.mark.usefixtures("logged")
async def test_the_refused_pending_candidate_is_not_sent_again() -> None:
    await _stored(pending="seller-secret")
    await mark_account_twofa_pending_refused("a", "seller-secret")
    client = _Client()

    await _rejected(client, await twofa_password_candidates("a"))

    assert client.sent == ["old-secret"]


def _submit(**fields: Any) -> PhoneCodeSubmit:
    return PhoneCodeSubmit(account_id="a", phone="+1", phone_code_hash="h", code="1", **fields)


@pytest.mark.asyncio
@pytest.mark.usefixtures("logged")
async def test_the_code_sign_in_blocks_stored_passwords_but_not_the_typed_one() -> None:
    await _stored()
    for _ in range(_MAX):
        await _rejected(_Client(), ["old-secret"])

    stored = _Client()
    with pytest.raises(TwofaAttemptsExhaustedError):
        await _auth._sign_in(stored, _submit(password="old-secret"))  # ty: ignore[invalid-argument-type]
    assert stored.sent == []

    typed = _Client("typed-secret")
    request = _submit(password="typed-secret", password_typed=True)
    assert await _auth._sign_in(typed, request) is True  # ty: ignore[invalid-argument-type]
    assert typed.sent == ["typed-secret"]
    assert await _left() == _MAX


@pytest.mark.asyncio
async def test_the_qr_password_is_always_sent_counted_and_cleared_by_success(
    logged: Logged,
) -> None:
    await _stored()
    session = _qr_login._QrSession(
        state=QrLoginState(account_id="a", status="password_needed"),
        deadline=asyncio.get_running_loop().time() + 30,
    )
    client = _Client("right")
    for password in ("wrong-1", "wrong-2", "wrong-3", "right"):
        session.passwords.put_nowait(password)

    assert await _qr_login._await_password(session, client) is True  # ty: ignore[invalid-argument-type]

    assert client.sent == ["wrong-1", "wrong-2", "wrong-3", "right"]
    # Three wrong answers spent the budget (journalled once); the accepted one gave it back.
    assert [event for event, _ in logged] == ["twofa_attempts_exhausted"]
    assert await _left() == _MAX


class _Passkey:
    account_id = "a"


@pytest.mark.asyncio
@pytest.mark.usefixtures("logged")
async def test_the_other_senders_report_the_spent_budget_with_one_code() -> None:
    exhausted = TwofaAttemptsExhaustedError()
    assert classify_rekey(exhausted).code == "twofa_attempts_exhausted"
    request = SecondDeviceSignIn(account_id="a", device_type="mobile", session_name="s")
    assert classify_device(request, exhausted).code == "twofa_attempts_exhausted"

    await _stored()
    for _ in range(_MAX):
        await _rejected(_Client(), ["old-secret"])
    with pytest.raises(PasskeyGatewayError) as raised:
        await _second_factor(_Client(), _Passkey())  # ty: ignore[invalid-argument-type]
    assert str(raised.value) == "twofa_attempts_exhausted"
