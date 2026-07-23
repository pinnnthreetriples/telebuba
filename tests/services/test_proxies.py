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
async def test_assign_and_unassign_evict_client(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing an account's proxy must evict its pooled client so it rebuilds."""
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreate(account_id="acc-1"))
    proxy = await proxies.add_proxy(ProxyCreate(proxy_type="socks5", host="h", port=1080))

    evicted: list[str] = []

    async def fake_evict(account_id: str) -> None:
        evicted.append(account_id)

    monkeypatch.setattr("services.proxies.evict_client", fake_evict)

    await proxies.assign_proxy(proxy.id, "acc-1")
    await proxies.unassign_proxy("acc-1")

    assert evicted == ["acc-1", "acc-1"]


@pytest.mark.asyncio
async def test_remove_proxy_evicts_all_assigned_clients(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting a proxy must evict the pooled client of every account on it."""
    configure_database(tmp_path / "telebuba.db")
    proxy = await proxies.add_proxy(ProxyCreate(proxy_type="socks5", host="h", port=1080))
    for index in range(2):
        await create_account(AccountCreate(account_id=f"acc-{index}"))
        await proxies.assign_proxy(proxy.id, f"acc-{index}")

    evicted: list[str] = []

    async def fake_evict(account_id: str) -> None:
        evicted.append(account_id)

    monkeypatch.setattr("services.proxies.evict_client", fake_evict)

    await proxies.remove_proxy(proxy.id)

    assert set(evicted) == {"acc-0", "acc-1"}


@pytest.mark.asyncio
async def test_add_proxy_credential_rotation_evicts_clients(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-adding an endpoint rotates creds on the same id and must evict its clients."""
    configure_database(tmp_path / "telebuba.db")
    proxy = await proxies.add_proxy(
        ProxyCreate(proxy_type="socks5", host="h", port=1080, password="old"),
    )
    await create_account(AccountCreate(account_id="acc-1"))
    await proxies.assign_proxy(proxy.id, "acc-1")

    evicted: list[str] = []

    async def fake_evict(account_id: str) -> None:
        evicted.append(account_id)

    monkeypatch.setattr("services.proxies.evict_client", fake_evict)

    rotated = await proxies.add_proxy(
        ProxyCreate(proxy_type="socks5", host="h", port=1080, password="new"),
    )

    assert rotated.id == proxy.id
    assert evicted == ["acc-1"]


@pytest.mark.asyncio
async def test_add_proxy_fresh_insert_evicts_nothing(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A brand-new proxy has no accounts, so adding it evicts no clients."""
    configure_database(tmp_path / "telebuba.db")

    evicted: list[str] = []

    async def fake_evict(account_id: str) -> None:
        evicted.append(account_id)

    monkeypatch.setattr("services.proxies.evict_client", fake_evict)

    await proxies.add_proxy(ProxyCreate(proxy_type="socks5", host="h", port=1080))

    assert evicted == []


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


@pytest.mark.asyncio
async def test_probe_proxy_does_not_persist(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_database(tmp_path / "telebuba.db")

    async def fake_check(_proxy: object) -> ProxyCheckResult:
        return ProxyCheckResult(status="tcp_working", country_code="DE")

    monkeypatch.setattr("services.proxies.check_proxy_connectivity", fake_check)

    result = await proxies.probe_proxy(ProxyCreate(proxy_type="https", host="h", port=8080))
    assert result.status == "tcp_working"
    assert (await proxies.list_pool()).proxies == []
