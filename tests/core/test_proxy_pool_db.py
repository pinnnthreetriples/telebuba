from __future__ import annotations

import pytest

from core.db import (
    ProxyCapacityError,
    assign_account_to_proxy,
    configure_database,
    create_account,
    create_proxy,
    delete_proxy,
    fetch_account_proxy_settings,
    fetch_proxy,
    list_accounts,
    list_proxies,
    unassign_account_from_proxy,
    update_proxy_check,
)
from schemas.accounts import AccountCreate
from schemas.proxy import ProxyCheckUpdate, ProxyCreate


async def _account(account_id: str) -> None:
    await create_account(AccountCreate(account_id=account_id))


@pytest.mark.asyncio
async def test_create_proxy_returns_masked_pool_entry(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    proxy = await create_proxy(
        ProxyCreate(
            proxy_type="socks5",
            host="nl.example",
            port=1080,
            username="alice",
            password="secret",
        ),
    )
    assert proxy.username == "a***e"
    assert proxy.has_password is True
    assert proxy.status == "unknown"
    assert (proxy.used, proxy.capacity, proxy.free) == (0, 3, 3)


@pytest.mark.asyncio
async def test_create_proxy_is_idempotent_on_identity(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    first = await create_proxy(ProxyCreate(proxy_type="socks5", host="h", port=1080, password="a"))
    second = await create_proxy(ProxyCreate(proxy_type="socks5", host="h", port=1080, password="b"))
    assert second.id == first.id
    pool = await list_proxies()
    assert len(pool.proxies) == 1


@pytest.mark.asyncio
async def test_assign_fills_slot_and_resolves_settings(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await _account("acc-1")
    proxy = await create_proxy(ProxyCreate(proxy_type="socks5", host="h", port=1080, password="p"))

    updated = await assign_account_to_proxy(proxy.id, "acc-1")
    assert (updated.used, updated.free) == (1, 2)

    settings = await fetch_account_proxy_settings("acc-1")
    assert settings is not None
    assert settings.password == "p"

    accounts = await list_accounts()
    row = accounts.accounts[0]
    assert row.proxy_id == proxy.id
    assert row.proxy_host == "h"


@pytest.mark.asyncio
async def test_assign_enforces_capacity(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    proxy = await create_proxy(ProxyCreate(proxy_type="socks5", host="h", port=1080))
    for index in range(3):
        await _account(f"acc-{index}")
        await assign_account_to_proxy(proxy.id, f"acc-{index}")
    await _account("acc-overflow")
    with pytest.raises(ProxyCapacityError):
        await assign_account_to_proxy(proxy.id, "acc-overflow")


@pytest.mark.asyncio
async def test_reassigning_same_account_is_idempotent(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    proxy = await create_proxy(ProxyCreate(proxy_type="socks5", host="h", port=1080))
    await _account("acc-1")
    await assign_account_to_proxy(proxy.id, "acc-1")
    again = await assign_account_to_proxy(proxy.id, "acc-1")
    assert again.used == 1


@pytest.mark.asyncio
async def test_assign_unknown_proxy_or_account_raises(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    proxy = await create_proxy(ProxyCreate(proxy_type="socks5", host="h", port=1080))
    await _account("acc-1")
    with pytest.raises(ValueError, match="Proxy not found"):
        await assign_account_to_proxy("missing", "acc-1")
    with pytest.raises(ValueError, match="Account not found"):
        await assign_account_to_proxy(proxy.id, "missing")


@pytest.mark.asyncio
async def test_unassign_clears_account_proxy(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    proxy = await create_proxy(ProxyCreate(proxy_type="socks5", host="h", port=1080))
    await _account("acc-1")
    await assign_account_to_proxy(proxy.id, "acc-1")

    await unassign_account_from_proxy("acc-1")

    assert await fetch_account_proxy_settings("acc-1") is None
    refreshed = await fetch_proxy(proxy.id)
    assert refreshed is not None
    assert refreshed.used == 0


@pytest.mark.asyncio
async def test_delete_proxy_detaches_accounts(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    proxy = await create_proxy(ProxyCreate(proxy_type="socks5", host="h", port=1080))
    await _account("acc-1")
    await assign_account_to_proxy(proxy.id, "acc-1")

    await delete_proxy(proxy.id)

    assert await fetch_proxy(proxy.id) is None
    assert await fetch_account_proxy_settings("acc-1") is None
    accounts = await list_accounts()
    assert accounts.accounts[0].proxy_id is None


@pytest.mark.asyncio
async def test_update_proxy_check_persists_geo(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await _account("acc-1")
    proxy = await create_proxy(ProxyCreate(proxy_type="socks5", host="h", port=1080))
    await assign_account_to_proxy(proxy.id, "acc-1")

    saved = await update_proxy_check(
        ProxyCheckUpdate(
            proxy_id=proxy.id,
            status="tcp_working",
            exit_ip="1.2.3.4",
            country_code="NL",
            country_name="Netherlands",
            asn="AS1 Hetzner",
            is_datacenter=True,
        ),
    )
    assert saved.status == "tcp_working"
    assert saved.country_code == "NL"
    assert saved.is_datacenter is True

    accounts = await list_accounts()
    assert accounts.accounts[0].proxy_country_code == "NL"


@pytest.mark.asyncio
async def test_update_proxy_check_missing_proxy_raises(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    with pytest.raises(ValueError, match="Proxy not found"):
        await update_proxy_check(ProxyCheckUpdate(proxy_id="missing", status="failed"))
