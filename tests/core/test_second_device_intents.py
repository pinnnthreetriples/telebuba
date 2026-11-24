"""The second device's journal and probe: a cut-short sign-in is repaired, never leaked."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import fetch_account
from core.repositories.account_key_intents import list_open_intents
from core.telegram_client._second_device import authorize_second_device
from schemas.device_fingerprint import DeviceFingerprint
from schemas.key_intent import KeyIntent
from tests.core import test_second_device as base
from tests.core.test_second_device import (
    _desktop_account,
    _NewDevice,
    _Primary,
    _request,
    _wire,
)

if TYPE_CHECKING:
    from pathlib import Path

# The second-device tests' autouse isolation (database, sessions folder, pool), reused.
_isolate = base._isolate


async def _intents() -> list[KeyIntent]:
    from core.db import _get_engine  # noqa: PLC0415

    def _read() -> list[KeyIntent]:
        with _get_engine().connect() as connection:
            rows = connection.exec_driver_sql("select * from account_key_intents").mappings().all()
        return [KeyIntent.model_validate(dict(row)) for row in rows]

    return await asyncio.to_thread(_read)


@pytest.mark.asyncio
async def test_a_recorded_sign_in_closes_its_intent_done(monkeypatch: pytest.MonkeyPatch) -> None:
    await _desktop_account()
    _wire(monkeypatch, _Primary([(10, "Login code: 11111")]))

    result = await authorize_second_device(_request())

    assert result.code is None
    (intent,) = await _intents()
    assert (intent.kind, intent.session_name, intent.device_type) == (
        "second_device",
        "acc_mobile",
        "mobile",
    )
    assert (intent.stage, intent.outcome) == ("signed_in", "done")
    # The fingerprint is kept so a repair can open the file as the device it was made as.
    assert intent.device_json is not None
    kept = DeviceFingerprint.model_validate_json(intent.device_json)
    assert (kept.account_id, kept.device_type) == ("acc", "mobile")


@pytest.mark.asyncio
async def test_a_refused_sign_in_closes_its_intent_undone(monkeypatch: pytest.MonkeyPatch) -> None:
    await _desktop_account()
    _wire(monkeypatch, _Primary([(10, "hi")]), code_text=None)

    result = await authorize_second_device(_request())

    assert result.code == "device_code_timeout"
    (intent,) = await _intents()
    assert (intent.stage, intent.outcome) == ("started", "undone")


@pytest.mark.asyncio
async def test_a_dropped_connection_after_the_server_accepted_is_probed_and_logged_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _desktop_account()
    made = _wire(
        monkeypatch,
        _Primary([(1, "hi")]),
        sign_in_error=ConnectionError("socket gone"),
        authorized=True,
    )

    result = await asyncio.wait_for(authorize_second_device(_request()), timeout=5)

    assert (result.code, result.error_type) == ("device_unreachable", "ConnectionError")
    assert "is_user_authorized" in made[0].calls
    assert "log_out" in made[0].calls
    assert not made[0].session_file.exists()
    (intent,) = await _intents()
    assert intent.outcome == "undone"


@pytest.mark.asyncio
async def test_a_refusal_by_telegram_is_not_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    from telethon import errors  # noqa: PLC0415

    await _desktop_account()
    made = _wire(
        monkeypatch,
        _Primary([(1, "hi")]),
        sign_in_error=errors.PhoneCodeInvalidError(request=None),
        authorized=True,
    )

    result = await authorize_second_device(_request())

    assert result.code == "device_code_invalid"
    assert "is_user_authorized" not in made[0].calls


@pytest.mark.asyncio
async def test_a_logout_that_is_not_confirmed_keeps_the_file_and_the_intent_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    await _desktop_account()
    monkeypatch.setattr(settings.telegram, "session_check_timeout_seconds", 0.05)
    made = _wire(monkeypatch, _Primary([(1, "hi")]), hang_sign_in=True, authorized=True)

    async def _fail(_self: _NewDevice) -> None:
        raise ConnectionError

    monkeypatch.setattr(_NewDevice, "log_out", _fail)

    result = await asyncio.wait_for(authorize_second_device(_request()), timeout=5)

    assert result.code == "device_unreachable"
    # The file is the only handle on a live authorization: kept for the repair.
    assert made[0].session_file.exists()
    assert made[0].session_file.is_relative_to(tmp_path)
    (intent,) = await list_open_intents("acc")
    assert (intent.kind, intent.session_name, intent.stage) == (
        "second_device",
        "acc_mobile",
        "started",
    )
    account = await fetch_account("acc")
    assert account is not None
    assert account.second_session_name is None
