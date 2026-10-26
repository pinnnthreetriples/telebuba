"""``VerifyTwoFactorPassword``: ``getPassword`` then ``getPasswordSettings``, nothing written."""

from __future__ import annotations

from typing import Any

import pytest
from telethon import errors
from telethon.tl.functions.account import GetPasswordRequest, GetPasswordSettingsRequest

from core.telegram_client import execute
from schemas.telegram_actions_twofa import VerifyTwoFactorPassword
from tests.core.telegram_client._twofa_doubles import PASSWORD, PROOF, Password, patch_srp
from tests.core.telegram_client.helpers import patch_action_client


class VerifyClient:
    """Only the two read RPCs; any write fails the test by raising."""

    def __init__(self, password: object, *, settings_error: Exception | None = None) -> None:
        self._password = password
        self._settings_error = settings_error
        self.requests: list[object] = []

    async def connect(self) -> None:
        return None

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        if isinstance(request, GetPasswordRequest):
            return self._password
        assert isinstance(request, GetPasswordSettingsRequest), "verify must not write"
        if self._settings_error is not None:
            raise self._settings_error
        return object()


@pytest.fixture
def stub_srp(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    return patch_srp(monkeypatch)


@pytest.mark.asyncio
async def test_the_right_password_sends_the_proof_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],
) -> None:
    client = VerifyClient(Password(has_password=True))
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", VerifyTwoFactorPassword(password=PASSWORD))

    assert result.status == "ok"
    assert [type(r) for r in client.requests] == [GetPasswordRequest, GetPasswordSettingsRequest]
    assert client.requests[1].password == PROOF  # ty: ignore[unresolved-attribute]
    assert [password for _pwd, password in stub_srp["check"]] == [PASSWORD]
    assert stub_srp["digest"] == []


@pytest.mark.asyncio
async def test_a_wrong_password_is_the_stable_refusal_without_the_password(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],  # noqa: ARG001
) -> None:
    client = VerifyClient(
        Password(has_password=True),
        settings_error=errors.rpcerrorlist.PasswordHashInvalidError(None),
    )
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", VerifyTwoFactorPassword(password=PASSWORD))

    assert result.status == "failed"
    assert result.error_message == "twofa_current_password_invalid"
    assert PASSWORD not in result.model_dump_json()


@pytest.mark.asyncio
async def test_an_account_without_2fa_is_not_enabled_and_spends_no_srp(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],
) -> None:
    client = VerifyClient(Password())
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", VerifyTwoFactorPassword(password=PASSWORD))

    assert result.error_message == "twofa_not_enabled"
    assert len(client.requests) == 1
    assert stub_srp["check"] == []


def test_the_password_is_not_in_repr() -> None:
    assert PASSWORD not in repr(VerifyTwoFactorPassword(password=PASSWORD))
