"""Tests for the proxy-pool service layer."""

from __future__ import annotations

import pytest

from core.db import configure_database, create_account
from schemas.accounts import AccountCreate
from schemas.proxy import ProxyCheckResult, ProxyCreate
from services import proxies


@pytest.mark.asyncio
async def test_add_and_list_pool(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await proxies.add_proxy(ProxyCreate(proxy_type="socks5", host="h", port=1080))
    pool = await proxies.list_pool()
    assert len(pool.proxies) == 1
    assert pool.proxies[0].used == 0


@pytest.mark.asyncio
async def test_assign_and_unassign(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreate(account_id="acc-1"))
    proxy = await proxies.add_proxy(ProxyCreate(proxy_type="socks5", host="h", port=1080))

    assigned = await proxies.assign_proxy(proxy.id, "acc-1")
    assert assigned.used == 1

    await proxies.unassign_proxy("acc-1")
    pool = await proxies.list_pool()
    assert pool.proxies[0].used == 0


@pytest.mark.asyncio
async def test_assign_over_capacity_raises(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    proxy = await proxies.add_proxy(ProxyCreate(proxy_type="socks5", host="h", port=1080))
    for index in range(3):
        await create_account(AccountCreate(account_id=f"acc-{index}"))
        await proxies.assign_proxy(proxy.id, f"acc-{index}")
    await create_account(AccountCreate(account_id="acc-overflow"))
    with pytest.raises(proxies.ProxyCapacityError):
        await proxies.assign_proxy(proxy.id, "acc-overflow")


@pytest.mark.asyncio
async def test_remove_proxy(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    proxy = await proxies.add_proxy(ProxyCreate(proxy_type="socks5", host="h", port=1080))
    await proxies.remove_proxy(proxy.id)
    pool = await proxies.list_pool()
    assert pool.proxies == []


@pytest.mark.asyncio
async def test_check_proxy_persists_result(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    proxy = await proxies.add_proxy(ProxyCreate(proxy_type="socks5", host="h", port=1080))

    async def fake_check(_proxy: object) -> ProxyCheckResult:
        return ProxyCheckResult(status="tcp_working", exit_ip="1.2.3.4", country_code="NL")

    monkeypatch.setattr("services.proxies.check_proxy_connectivity", fake_check)

    checked = await proxies.check_proxy(proxy.id)
    assert checked.status == "tcp_working"
    assert checked.country_code == "NL"


@pytest.mark.asyncio
async def test_check_proxy_unknown_raises(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    with pytest.raises(ValueError, match="Proxy not found"):
        await proxies.check_proxy("missing")
