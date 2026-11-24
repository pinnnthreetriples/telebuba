"""Adversarial probe of the second-device journal: a cancellation mid sign-in."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.telegram_client import _second_device as sd


class _Client:
    def __init__(self) -> None:
        self.logged_out = False

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def is_user_authorized(self) -> bool:
        return True  # Telegram accepted the sign-in before the cancellation.

    async def log_out(self) -> bool:
        self.logged_out = True
        return True


@pytest.mark.asyncio
async def test_a_cancel_during_the_sign_in_logs_out_an_accepted_authorization(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    async def _cancelled(*_a: object, **_k: object) -> None:
        raise asyncio.CancelledError

    async def _remove(_path: str) -> None:
        return None

    monkeypatch.setattr(sd, "_sign_in", _cancelled)
    monkeypatch.setattr(sd, "_remove_session_file", _remove)
    client = _Client()
    request = SimpleNamespace(account_id="acc", session_name="acc_second")

    with pytest.raises(asyncio.CancelledError):
        await sd._attempt(request, client, str(tmp_path), None, None, None)  # ty: ignore[invalid-argument-type]

    assert client.logged_out
