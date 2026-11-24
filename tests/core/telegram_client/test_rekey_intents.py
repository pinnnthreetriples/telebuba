"""The re-key's journal and deadlines: a cut-short attempt leaves what a repair needs."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from typing import TYPE_CHECKING

import pytest
from telethon import errors
from telethon.tl.functions.account import ChangeAuthorizationSettingsRequest
from telethon.tl.functions.auth import AcceptLoginTokenRequest

from core.config import settings
from core.db import _get_engine
from core.repositories.account_key_intents import fetch_intent, list_open_intents
from core.telegram_client import _rekey
from core.telegram_client._rekey import RekeyGatewayError
from schemas.key_intent import KeyIntent
from tests.core.telegram_client import test_rekey as base
from tests.core.telegram_client.test_rekey import (
    _NEW_HASH,
    _OLD_HASH,
    _Check,
    _fail_on,
    _files,
    _run,
)

if TYPE_CHECKING:
    from tests.core.telegram_client.test_rekey import _Rig


# The re-key tests' fake world (approver, new client, files), reused as is.
rig = base.rig


def _read_all() -> list[KeyIntent]:
    with _get_engine().connect() as connection:
        rows = connection.exec_driver_sql("select * from account_key_intents").mappings().all()
    return [KeyIntent.model_validate(dict(row)) for row in rows]


async def _all_intents() -> list[KeyIntent]:
    return await asyncio.to_thread(_read_all)


async def _open_intents() -> list[KeyIntent]:
    return await list_open_intents("acc-1")


@pytest.mark.asyncio
async def test_a_finished_swap_leaves_its_intent_open_for_the_service_to_close(
    rig: _Rig,  # noqa: ARG001 - the fake world, patched in by the fixture
) -> None:
    result = await _run()

    intent = await fetch_intent(result.intent_id)  # ty: ignore[unresolved-attribute]
    assert intent is not None
    assert (intent.kind, intent.stage, intent.session_name) == ("rekey", "swapped", "acc_rekey")
    assert (intent.old_hash, intent.new_hash) == (str(_OLD_HASH), str(_NEW_HASH))
    # The one-way mark of the key the account's file held BEFORE the swap: never the key.
    assert intent.old_key_mark == hashlib.sha256(b"old").hexdigest()
    assert intent.closed_at is None


@pytest.mark.asyncio
async def test_a_login_accepted_but_never_heard_logs_the_new_authorization_out(rig: _Rig) -> None:
    # The approver accepted the token; the update that completes the login never came.
    rig.new.wait_failure = TimeoutError()

    with pytest.raises(RekeyGatewayError) as refused:
        await _run()

    assert refused.value.code == "rekey_timeout"
    assert "log_out" in rig.new.events
    assert _files(rig) == {"acc.session": "old"}
    (intent,) = await _all_intents()
    assert (intent.stage, intent.new_hash, intent.outcome) == ("signed_in", "222", "undone")


@pytest.mark.asyncio
async def test_a_failure_before_the_acceptance_removes_the_scratch_and_closes_the_intent(
    rig: _Rig,
) -> None:
    async def _no_token() -> object:
        raise ConnectionError

    rig.new.qr_login = _no_token  # ty: ignore[invalid-assignment]

    with pytest.raises(RekeyGatewayError):
        await _run()

    assert "log_out" not in rig.new.events
    assert _files(rig) == {"acc.session": "old"}
    (intent,) = await _all_intents()
    assert (intent.stage, intent.outcome) == ("started", "undone")


@pytest.mark.asyncio
async def test_a_logout_that_is_not_confirmed_keeps_the_scratch_file_and_the_intent_open(
    rig: _Rig,
) -> None:
    rig.new.wait_failure = TimeoutError()

    async def _fail() -> None:
        raise ConnectionError

    rig.new.log_out = _fail  # ty: ignore[invalid-assignment]

    with pytest.raises(RekeyGatewayError):
        await _run()

    # The scratch file is the only handle on a live authorization: it stays for the repair.
    assert _files(rig) == {"acc.session": "old", "acc_rekey.session": "new"}
    (intent,) = await _open_intents()
    assert (intent.stage, intent.new_hash) == ("signed_in", "222")


@pytest.mark.asyncio
async def test_a_logout_telegram_refused_is_not_a_confirmation_either(rig: _Rig) -> None:
    rig.new.wait_failure = TimeoutError()

    async def _refused() -> bool:
        return False

    rig.new.log_out = _refused  # ty: ignore[invalid-assignment]

    with pytest.raises(RekeyGatewayError):
        await _run()

    assert "acc_rekey.session" in _files(rig)
    assert len(await _open_intents()) == 1


@pytest.mark.asyncio
async def test_a_hung_check_of_the_account_file_times_out_and_the_imported_content_returns(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.telegram, "session_check_timeout_seconds", 0.05)

    async def _hang(_self: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(_Check, "connect", _hang)

    with pytest.raises(RekeyGatewayError) as refused:
        await asyncio.wait_for(_run(), timeout=10)

    assert refused.value.code == "rekey_swap_failed"
    assert refused.value.error_type == "TimeoutError"
    # The imported content is back in the account's file and the new login was ended.
    assert _files(rig) == {"acc.session": "old"}
    assert "log_out" in rig.new.events


@pytest.mark.asyncio
async def test_a_hung_connect_of_the_new_client_is_bounded(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.telegram, "session_check_timeout_seconds", 0.05)

    async def _hang() -> None:
        await asyncio.Event().wait()

    rig.new.connect = _hang  # ty: ignore[invalid-assignment]

    with pytest.raises(RekeyGatewayError) as refused:
        await asyncio.wait_for(_run(), timeout=10)

    assert refused.value.code == "rekey_timeout"
    assert _files(rig) == {"acc.session": "old"}


@pytest.mark.asyncio
async def test_a_failed_api_record_after_the_swap_does_not_fail_the_rekey(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _record(_profile: object) -> None:
        raise sqlite3.OperationalError

    monkeypatch.setattr(_rekey, "record_key_api", _record)

    result = await _run()

    assert result.new_hash == str(_NEW_HASH)  # ty: ignore[unresolved-attribute]
    assert _files(rig) == {"acc.session": "new"}


@pytest.mark.asyncio
async def test_a_swap_that_cannot_be_put_back_closes_the_intent_incomplete(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.check_user = 999
    _fail_on(monkeypatch, "copy_session", sqlite3.OperationalError("locked"), source=rig.aside)

    with pytest.raises(RekeyGatewayError) as refused:
        await _run()

    assert refused.value.code == "rekey_incomplete"
    (intent,) = await _all_intents()
    assert (intent.stage, intent.old_hash, intent.outcome) == ("signed_in", "111", "incomplete")


def _approver_failing_on(
    monkeypatch: pytest.MonkeyPatch, request_type: type, failure: BaseException
) -> None:
    """Make the approver raise ``failure`` for one kind of request only."""
    answer = base._Approver.__call__

    async def _call(self: base._Approver, request: object) -> object:
        if isinstance(request, request_type):
            self.requests.append(request)
            raise failure
        return await answer(self, request)

    monkeypatch.setattr(base._Approver, "__call__", _call)


@pytest.mark.asyncio
async def test_an_acceptance_that_got_no_answer_is_undone_as_if_it_had_landed(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A timeout is not a refusal: Telegram may have created the authorization.
    _approver_failing_on(monkeypatch, AcceptLoginTokenRequest, TimeoutError())

    with pytest.raises(RekeyGatewayError):
        await _run()

    assert "log_out" in rig.new.events
    (intent,) = await _all_intents()
    assert (intent.stage, intent.new_hash) == ("signed_in", None)


@pytest.mark.asyncio
async def test_an_acceptance_telegram_refused_made_nothing_to_undo(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _approver_failing_on(
        monkeypatch, AcceptLoginTokenRequest, errors.AuthTokenExpiredError(request=None)
    )

    with pytest.raises(RekeyGatewayError):
        await _run()

    assert "log_out" not in rig.new.events
    assert _files(rig) == {"acc.session": "old"}
    (intent,) = await _all_intents()
    assert (intent.stage, intent.outcome) == ("started", "undone")


@pytest.mark.asyncio
async def test_a_confirmation_that_fails_does_not_fail_the_login(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.approver.unconfirmed = True
    _approver_failing_on(monkeypatch, ChangeAuthorizationSettingsRequest, TimeoutError())

    result = await _run()

    assert result.new_hash == str(_NEW_HASH)  # ty: ignore[unresolved-attribute]
    assert _files(rig) == {"acc.session": "new"}
