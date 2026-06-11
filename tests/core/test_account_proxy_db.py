from __future__ import annotations

import pytest

from core.db import (
    configure_database,
    create_account,
    delete_account_proxy,
    fetch_account_proxy,
    fetch_account_proxy_settings,
    list_accounts,
    upsert_account_proxy,
)
from schemas.accounts import AccountCreate
from schemas.proxy import AccountProxyDelete, AccountProxyUpsert


@pytest.mark.asyncio
async def test_upsert_account_proxy_returns_masked_read_model(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreate(account_id="acc-1"))

    saved = await upsert_account_proxy(
        AccountProxyUpsert(
            account_id="acc-1",
            proxy_type="socks5",
            host="127.0.0.1",
            port=9050,
            username="alice",
            password="secret",  # noqa: S106 - test fixture value, not a real credential.
        ),
    )

    assert saved.account_id == "acc-1"
    assert saved.username == "a***e"
    assert saved.has_password is True
    assert saved.status == "unknown"

    settings = await fetch_account_proxy_settings("acc-1")
    assert settings is not None
    assert settings.password == "secret"  # noqa: S105 - test fixture value.

    accounts = await list_accounts()
    assert accounts.accounts[0].proxy_type == "socks5"
    assert accounts.accounts[0].proxy_host == "127.0.0.1"


@pytest.mark.asyncio
async def test_delete_account_proxy_removes_settings(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreate(account_id="acc-2"))
    await upsert_account_proxy(
        AccountProxyUpsert(
            account_id="acc-2",
            proxy_type="http",
            host="proxy.local",
            port=8080,
        ),
    )

    await delete_account_proxy(AccountProxyDelete(account_id="acc-2"))

    assert await fetch_account_proxy("acc-2") is None
    assert await fetch_account_proxy_settings("acc-2") is None
