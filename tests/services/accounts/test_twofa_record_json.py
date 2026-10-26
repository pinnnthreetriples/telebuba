"""``record_account_twofa_from_json``: the seller JSON's password, after the proxy exists."""

from __future__ import annotations

import json

import pytest

from core.db import create_account, fetch_account_twofa_password
from core.repositories.proxies import create_and_assign_proxy
from schemas.accounts import AccountCreate
from schemas.proxy import ProxyCreateAssignment
from services.accounts import (
    AccountActionError,
    AccountNotFoundError,
    record_account_twofa_from_json,
)
from tests.services.accounts._twofa_support import patch_execute

FAKE = "fake-pass-1234567"


def _json(**raw: object) -> bytes:
    return json.dumps({"phone": "79990001122", **raw}).encode()


async def _account(*, proxy: bool) -> None:
    await create_account(AccountCreate(account_id="rec"))
    if proxy:
        await create_and_assign_proxy(
            ProxyCreateAssignment(
                proxy_type="socks5", host="10.0.0.5", port=1080, account_id="rec"
            ),
        )


@pytest.mark.asyncio
async def test_the_json_password_is_verified_and_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    await _account(proxy=True)
    actions = patch_execute(monkeypatch)

    assert await record_account_twofa_from_json("rec", _json(twoFA=FAKE)) is True

    assert len(actions) == 1
    assert await fetch_account_twofa_password("rec") == FAKE


@pytest.mark.asyncio
async def test_a_broken_json_is_refused_before_any_telegram_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account(proxy=True)
    actions = patch_execute(monkeypatch)

    with pytest.raises(AccountActionError) as excinfo:
        await record_account_twofa_from_json("rec", b"nope")

    assert str(excinfo.value) == "session_json_invalid"
    assert actions == []


@pytest.mark.asyncio
async def test_a_json_without_a_password_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    await _account(proxy=True)
    actions = patch_execute(monkeypatch)

    with pytest.raises(AccountActionError) as excinfo:
        await record_account_twofa_from_json("rec", _json())

    assert str(excinfo.value) == "session_json_no_password"
    assert actions == []


@pytest.mark.asyncio
async def test_no_proxy_is_refused_before_any_telegram_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account(proxy=False)
    actions = patch_execute(monkeypatch)

    with pytest.raises(AccountActionError) as excinfo:
        await record_account_twofa_from_json("rec", _json(twoFA=FAKE))

    assert str(excinfo.value) == "twofa_proxy_missing"
    assert actions == []
    assert await fetch_account_twofa_password("rec") is None


@pytest.mark.asyncio
async def test_an_unknown_account_is_not_found() -> None:
    with pytest.raises(AccountNotFoundError):
        await record_account_twofa_from_json("ghost", _json(twoFA=FAKE))
