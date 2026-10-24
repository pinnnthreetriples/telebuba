"""Passkey persistence: one row per account, removed with the account."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import IntegrityError

from core.db import configure_database, create_account
from core.repositories._accounts_passkey import (
    delete_account_passkey,
    fetch_account_passkey,
    save_account_passkey,
)
from core.repositories.accounts import _delete_account
from schemas.accounts import AccountCreate
from schemas.passkey import StoredPasskey

if TYPE_CHECKING:
    from pathlib import Path


def _passkey(account_id: str = "acc-pk") -> StoredPasskey:
    return StoredPasskey(
        account_id=account_id,
        credential_id="cred-1",
        user_handle="1:257221571",
        vault_key_name=f"tb-{account_id}",
        created_at="2026-10-24T12:00:00+00:00",
    )


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


@pytest.mark.asyncio
async def test_a_saved_passkey_reads_back_and_deletes() -> None:
    await create_account(AccountCreate(account_id="acc-pk"))
    assert await fetch_account_passkey("acc-pk") is None

    await save_account_passkey(_passkey())
    assert await fetch_account_passkey("acc-pk") == _passkey()

    await delete_account_passkey("acc-pk")
    assert await fetch_account_passkey("acc-pk") is None


@pytest.mark.asyncio
async def test_an_account_holds_one_passkey_at_most() -> None:
    await create_account(AccountCreate(account_id="acc-pk"))
    await save_account_passkey(_passkey())
    with pytest.raises(IntegrityError):
        await save_account_passkey(_passkey())


@pytest.mark.asyncio
async def test_deleting_the_account_removes_its_passkey_row() -> None:
    await create_account(AccountCreate(account_id="acc-pk"))
    await create_account(AccountCreate(account_id="acc-other"))
    await save_account_passkey(_passkey())
    await save_account_passkey(_passkey("acc-other"))

    await asyncio.to_thread(_delete_account, "acc-pk")

    assert await fetch_account_passkey("acc-pk") is None
    assert await fetch_account_passkey("acc-other") is not None
