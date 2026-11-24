"""The old-key checklist only ticks what a fresh login could actually use.

``twofa_stored`` is ok for a password Telegram ACCEPTED (the current column), never for the
seller's unverified candidate; ``passkey_stored`` needs the row AND a vault that answers
that the signing key is there. No Telegram call is made by the checklist.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.db import (
    create_account,
    fetch_account,
    set_account_twofa_password,
    set_account_twofa_pending,
)
from core.passkey_vault import PasskeyVaultCode, PasskeyVaultError
from core.repositories._accounts_passkey import save_account_passkey
from schemas.accounts import AccountCreate, AccountRead
from schemas.passkey import StoredPasskey
from schemas.session_rekey import StoredRekey
from services.accounts import _old_key_checklist as checklist
from services.accounts import passkey as passkey_module

_NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
_ID = "chk-1"


async def _account() -> AccountRead:
    await create_account(AccountCreate(account_id=_ID))
    account = await fetch_account(_ID)
    assert account is not None
    return account


async def _results(account: AccountRead) -> dict[str, bool]:
    rekey = StoredRekey(
        account_id=_ID, old_hash="111", rekeyed_at=(_NOW - timedelta(days=8)).isoformat()
    )
    items = await checklist.old_key_checklist(account, rekey, now=_NOW)
    return {item.name: item.ok for item in items}


async def _save_passkey() -> None:
    await save_account_passkey(
        StoredPasskey(
            account_id=_ID,
            credential_id="Y3JlZA",
            user_handle="1:257221571",
            vault_key_name="tb-chk-1",
            created_at=_NOW.isoformat(),
        )
    )


def _vault(
    monkeypatch: pytest.MonkeyPatch,
    *,
    exists: bool = True,
    error: Exception | None = None,
    configured: bool = True,
) -> list[str]:
    """The vault seams ``check_passkey_vault`` calls; the list records the keys asked about."""
    asked: list[str] = []

    async def _exists(name: str) -> bool:
        asked.append(name)
        if error is not None:
            raise error
        return exists

    async def _log(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(passkey_module, "signing_key_exists", _exists)
    monkeypatch.setattr(passkey_module, "passkey_vault_configured", lambda: configured)
    monkeypatch.setattr(passkey_module, "log_event", _log)
    return asked


@pytest.mark.asyncio
async def test_the_item_names_and_their_order_are_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vault(monkeypatch)
    account = await _account()
    rekey = StoredRekey(account_id=_ID, old_hash="111", rekeyed_at=_NOW.isoformat())

    items = await checklist.old_key_checklist(account, rekey, now=_NOW)

    assert [item.name for item in items] == [
        "spam_clean",
        "session_age",
        "fallback_session",
        "twofa_stored",
        "passkey_stored",
    ]


@pytest.mark.asyncio
async def test_an_unverified_seller_password_does_not_tick_twofa_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vault(monkeypatch)
    account = await _account()
    await set_account_twofa_pending(_ID, "seller-json-pw")

    assert (await _results(account))["twofa_stored"] is False


@pytest.mark.asyncio
async def test_a_password_telegram_accepted_ticks_twofa_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vault(monkeypatch)
    account = await _account()
    await set_account_twofa_password(_ID, "verified-pw")

    assert (await _results(account))["twofa_stored"] is True


@pytest.mark.asyncio
async def test_a_current_password_beside_a_candidate_still_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vault(monkeypatch)
    account = await _account()
    await set_account_twofa_password(_ID, "verified-pw")
    await set_account_twofa_pending(_ID, "later-pw")

    assert (await _results(account))["twofa_stored"] is True


@pytest.mark.asyncio
async def test_no_password_at_all_does_not_tick_twofa_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vault(monkeypatch)
    account = await _account()

    assert (await _results(account))["twofa_stored"] is False


@pytest.mark.asyncio
async def test_a_passkey_with_a_healthy_vault_ticks(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _vault(monkeypatch)
    account = await _account()
    await _save_passkey()

    assert (await _results(account))["passkey_stored"] is True
    assert asked == ["tb-chk-1"]


@pytest.mark.asyncio
async def test_a_passkey_row_whose_vault_key_is_missing_does_not_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _vault(monkeypatch, exists=False)
    account = await _account()
    await _save_passkey()

    assert (await _results(account))["passkey_stored"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    ["passkey_vault_sealed", "passkey_vault_unreachable", "passkey_vault_forbidden"],
)
async def test_a_passkey_row_with_a_vault_that_cannot_answer_does_not_tick(
    monkeypatch: pytest.MonkeyPatch, code: PasskeyVaultCode
) -> None:
    _vault(monkeypatch, error=PasskeyVaultError(code, "http_503"))
    account = await _account()
    await _save_passkey()

    assert (await _results(account))["passkey_stored"] is False


@pytest.mark.asyncio
async def test_no_passkey_row_does_not_tick_and_never_asks_the_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _vault(monkeypatch)
    account = await _account()

    assert (await _results(account))["passkey_stored"] is False
    assert asked == []


@pytest.mark.asyncio
async def test_an_unconfigured_vault_does_not_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    _vault(monkeypatch, configured=False)
    account = await _account()
    await _save_passkey()

    assert (await _results(account))["passkey_stored"] is False
