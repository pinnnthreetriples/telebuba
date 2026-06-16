from __future__ import annotations

import pytest

from core.db import (
    configure_database,
    create_account,
    delete_account_proxy,
    exit_ip_collisions,
    fetch_account_proxy,
    fetch_account_proxy_settings,
    list_accounts,
    update_account_proxy_check,
    upsert_account_proxy,
)
from schemas.accounts import AccountCreate
from schemas.proxy import AccountProxyCheckUpdate, AccountProxyDelete
from tests.factories import AccountProxyUpsertFactory


@pytest.mark.asyncio
async def test_upsert_account_proxy_returns_masked_read_model(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreate(account_id="acc-1"))

    saved = await upsert_account_proxy(
        AccountProxyUpsertFactory.build(
            account_id="acc-1",
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
async def test_update_account_proxy_check_persists_exit_country(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreate(account_id="acc-check"))
    await upsert_account_proxy(
        AccountProxyUpsertFactory.build(
            account_id="acc-check",
            port=9050,
        ),
    )

    proxy = await update_account_proxy_check(
        AccountProxyCheckUpdate(
            account_id="acc-check",
            status="tcp_working",
            exit_ip="45.130.253.155",
            country_code="NL",
            country_name="Netherlands",
        ),
    )
    accounts = await list_accounts()

    assert proxy.status == "tcp_working"
    assert proxy.exit_ip == "45.130.253.155"
    assert proxy.country_code == "NL"
    assert accounts.accounts[0].proxy_exit_ip == "45.130.253.155"
    assert accounts.accounts[0].proxy_country_name == "Netherlands"


@pytest.mark.asyncio
async def test_update_account_proxy_check_persists_asn_and_datacenter(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreate(account_id="acc-dc"))
    await upsert_account_proxy(
        AccountProxyUpsertFactory.build(account_id="acc-dc", port=9050),
    )

    proxy = await update_account_proxy_check(
        AccountProxyCheckUpdate(
            account_id="acc-dc",
            status="tcp_working",
            exit_ip="1.2.3.4",
            asn="AS24940 Hetzner Online GmbH",
            is_datacenter=True,
        ),
    )

    assert proxy.asn == "AS24940 Hetzner Online GmbH"
    assert proxy.is_datacenter is True


@pytest.mark.asyncio
async def test_exit_ip_collisions_flags_shared_ip(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    for account_id in ("acc-a", "acc-b", "acc-solo"):
        await create_account(AccountCreate(account_id=account_id))
        await upsert_account_proxy(
            AccountProxyUpsertFactory.build(
                account_id=account_id,
                port=9050,
            ),
        )
    shared = "8.8.8.8"
    await update_account_proxy_check(
        AccountProxyCheckUpdate(account_id="acc-a", status="tcp_working", exit_ip=shared),
    )
    await update_account_proxy_check(
        AccountProxyCheckUpdate(account_id="acc-b", status="tcp_working", exit_ip=shared),
    )
    await update_account_proxy_check(
        AccountProxyCheckUpdate(account_id="acc-solo", status="tcp_working", exit_ip="9.9.9.9"),
    )

    collisions = await exit_ip_collisions()

    assert set(collisions) == {shared}
    assert sorted(collisions[shared]) == ["acc-a", "acc-b"]


@pytest.mark.asyncio
async def test_delete_account_proxy_removes_settings(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreate(account_id="acc-2"))
    await upsert_account_proxy(
        AccountProxyUpsertFactory.build(
            account_id="acc-2",
            proxy_type="http",
            host="proxy.local",
            port=8080,
        ),
    )

    await delete_account_proxy(AccountProxyDelete(account_id="acc-2"))

    assert await fetch_account_proxy("acc-2") is None
    assert await fetch_account_proxy_settings("acc-2") is None
