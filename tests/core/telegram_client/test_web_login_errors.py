"""Which accept failures mean "that token rotated" and which mean "ask again"."""

from __future__ import annotations

import pytest
from telethon import errors

from core.telegram_client import WebLoginTransientError, accept_web_login_token

_MODULE = "core.telegram_client._web_login"


def _failing(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    async def fake_get_client(_account_id: str, _device_type: str | None = None) -> object:
        async def confirmer(_request: object) -> None:
            raise error

        return confirmer

    monkeypatch.setattr(f"{_MODULE}.get_client", fake_get_client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    [
        errors.AuthTokenExpiredError,
        errors.AuthTokenAlreadyAcceptedError,
        errors.AuthTokenInvalidxError,
        errors.AuthTokenInvalidError,
        errors.AuthTokenInvalid2Error,
        errors.AuthTokenExceptionError,
    ],
)
async def test_every_token_rotation_error_is_a_refusal_not_a_failure(
    monkeypatch: pytest.MonkeyPatch, error_type: type[errors.RPCError]
) -> None:
    _failing(monkeypatch, error_type(request=None))  # ty: ignore[missing-argument]

    assert await accept_web_login_token("acc-1", b"t") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ConnectionError(), TimeoutError(), OSError()])
async def test_a_connection_fault_is_transient(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    _failing(monkeypatch, error)

    with pytest.raises(WebLoginTransientError):
        await accept_web_login_token("acc-1", b"t")


@pytest.mark.asyncio
async def test_a_short_flood_wait_is_transient_and_carries_its_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flood = errors.FloodWaitError(request=None, capture=4)
    _failing(monkeypatch, flood)

    with pytest.raises(WebLoginTransientError) as caught:
        await accept_web_login_token("acc-1", b"t")

    assert caught.value.retry_after == 4.0


@pytest.mark.asyncio
async def test_a_long_flood_wait_is_a_real_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _failing(monkeypatch, errors.FloodWaitError(request=None, capture=3600))

    with pytest.raises(errors.FloodWaitError):
        await accept_web_login_token("acc-1", b"t")
