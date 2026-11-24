"""A second device is only skipped on PROOF its key is gone, not on any 406.

``_SESSION_ERRORS`` holds the BASE ``errors.AuthKeyError`` (HTTP 406) and Telethon's own
``AuthKeyNotFound`` (a transport 404 its docstring says may be temporary). Skipping a LIVE
second device makes its own session read as foreign, and a close could then end it.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest
from telethon import errors
from telethon.errors.common import AuthKeyNotFound
from telethon.tl.functions.account import GetAuthorizationsRequest, ResetAuthorizationRequest

from core.telegram_client import _session_audit, _session_close
from core.telegram_client._pool import TelegramClientPoolError
from core.telegram_client._session_audit import SessionAuditGatewayError, read_account_sessions
from core.telegram_client._session_close import close_sessions


def _live(*hashes: int) -> SimpleNamespace:
    entries = [SimpleNamespace(hash=0, current=True)]
    entries += [SimpleNamespace(hash=h, current=False) for h in hashes]
    return SimpleNamespace(authorization_ttl_days=180, authorizations=entries)


class _Client:
    def __init__(self, reply: object) -> None:
        self.reply = reply
        self.reset: list[int] = []

    async def __call__(self, request: object) -> object:
        if isinstance(request, ResetAuthorizationRequest):
            self.reset.append(request.hash)
            return True
        assert isinstance(request, GetAuthorizationsRequest)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _borrow(monkeypatch: pytest.MonkeyPatch, clients: dict[str, _Client]) -> None:
    async def _get_client(_account_id: str, device: str | None = None) -> _Client:
        return clients[str(device)]

    monkeypatch.setattr(_session_audit, "get_client", _get_client)
    monkeypatch.setattr(_session_close, "get_client", _get_client)


def _not_a_dead_key() -> list[Exception]:
    return [
        # What Telethon makes of ANY unknown 406.
        errors.AuthKeyError(request=None, message="SOME_NEW_406"),
        errors.UpdateAppToLoginError(request=None),
        errors.FreshResetAuthorisationForbiddenError(request=None),
        AuthKeyNotFound(),  # "temporarily or permanently" per Telethon
    ]


@pytest.mark.parametrize("failure", _not_a_dead_key(), ids=lambda e: type(e).__name__)
@pytest.mark.asyncio
async def test_a_second_device_with_a_406_that_is_not_a_dead_key_still_fails_the_read(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    _borrow(monkeypatch, {"desktop": _Client(_live(22)), "mobile": _Client(failure)})

    with pytest.raises(SessionAuditGatewayError):
        await read_account_sessions("acc-1", ["desktop", "mobile"])


@pytest.mark.parametrize("failure", _not_a_dead_key(), ids=lambda e: type(e).__name__)
@pytest.mark.asyncio
async def test_the_same_inside_a_pool_refusal_still_fails_the_read(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    _borrow(
        monkeypatch,
        {
            "desktop": _Client(_live(22)),
            "mobile": _Client(TelegramClientPoolError("acc-1", failure)),
        },
    )

    with pytest.raises(SessionAuditGatewayError):
        await read_account_sessions("acc-1", ["desktop", "mobile"])


@pytest.mark.asyncio
async def test_a_transiently_unknown_key_never_lets_a_close_end_our_live_second_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 22 is the live mobile device's own entry in the desktop's list.
    desktop = _Client(_live(22))
    _borrow(monkeypatch, {"desktop": desktop, "mobile": _Client(AuthKeyNotFound())})

    with contextlib.suppress(SessionAuditGatewayError):
        await close_sessions("acc-1", ["desktop", "mobile"], ["22"], [])

    assert desktop.reset == []
