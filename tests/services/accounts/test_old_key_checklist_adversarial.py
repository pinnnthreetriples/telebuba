"""The old-key checklist must survive a misbehaving vault and must not call Telegram.

The card is read right before a point of no return: an exception from the vault probe
would turn the whole card into a 500 instead of an unticked ``passkey_stored``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from core import passkey_vault
from core.db import create_account, fetch_account
from core.repositories._accounts_passkey import save_account_passkey
from schemas.accounts import AccountCreate
from schemas.passkey import StoredPasskey
from schemas.session_rekey import StoredRekey
from services.accounts import _old_key_checklist as checklist
from services.accounts import passkey as passkey_module

_NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
_ID = "chk-adv"


async def _card() -> dict[str, bool]:
    await create_account(AccountCreate(account_id=_ID))
    await save_account_passkey(
        StoredPasskey(
            account_id=_ID,
            credential_id="Y3JlZA",
            user_handle="1:257221571",
            vault_key_name="tb-chk-adv",
            created_at=_NOW.isoformat(),
        )
    )
    account = await fetch_account(_ID)
    assert account is not None
    rekey = StoredRekey(account_id=_ID, old_hash="111", rekeyed_at=_NOW.isoformat())
    items = await checklist.old_key_checklist(account, rekey, now=_NOW)
    return {item.name: item.ok for item in items}


def _silence_journal(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []

    async def _log(_level: str, event: str, **_kwargs: object) -> None:
        events.append(event)

    monkeypatch.setattr(passkey_module, "log_event", _log)
    monkeypatch.setattr(passkey_module, "passkey_vault_configured", lambda: True)
    return events


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("boom"), OSError("socket"), AttributeError("x")])
async def test_an_unexpected_exception_from_the_vault_probe_does_not_break_the_card(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _silence_journal(monkeypatch)

    async def _boom(_name: str) -> bool:
        raise error

    monkeypatch.setattr(passkey_module, "signing_key_exists", _boom)

    assert (await _card())["passkey_stored"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [[], None, "ok", 7])
async def test_a_200_with_a_non_object_json_body_does_not_break_the_card(
    monkeypatch: pytest.MonkeyPatch, body: object
) -> None:
    """A proxy in front of the vault answering ``200 []`` must read as "not ok", not crash."""
    _silence_journal(monkeypatch)

    async def _request(
        _method: str, _path: str, _body: dict[str, object] | None = None
    ) -> httpx.Response:
        return httpx.Response(200, json=body)

    monkeypatch.setattr(passkey_vault, "_request", _request)

    assert (await _card())["passkey_stored"] is False


@pytest.mark.asyncio
async def test_building_the_card_makes_no_telegram_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _silence_journal(monkeypatch)

    async def _exists(_name: str) -> bool:
        return True

    async def _telegram(*_args: object, **_kwargs: object) -> None:
        pytest.fail("the checklist reached Telegram")

    monkeypatch.setattr(passkey_module, "signing_key_exists", _exists)
    for target in (
        "core.telegram_client.execute",
        "core.telegram_client.execute_read",
        "services.accounts.twofa.execute",
        "services.accounts.twofa.execute_read",
    ):
        monkeypatch.setattr(target, _telegram)

    assert (await _card())["passkey_stored"] is True
