from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread

import pytest
from sqlalchemy.engine import Connection

from core.db import (
    ProxyCapacityError,
    assign_account_to_proxy,
    configure_database,
    create_account,
    create_and_assign_proxy,
    create_proxy,
    delete_proxy,
    fetch_account_proxy_settings,
    fetch_proxy,
    list_accounts,
    list_proxies,
    unassign_account_from_proxy,
    update_proxy_check,
)
from core.repositories import proxies as proxy_repository
from schemas.accounts import AccountCreate
from schemas.proxy import ProxyCheckUpdate, ProxyCreate, ProxyCreateAssignment


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
    first = await create_proxy(
        ProxyCreate(proxy_type="socks5", host="Example.COM.", port=1080, password="a"),
    )
    second = await create_proxy(
        ProxyCreate(proxy_type="socks5", host="example.com", port=1080, password="b"),
    )
    assert second.id == first.id
    pool = await list_proxies()
    assert len(pool.proxies) == 1


@pytest.mark.asyncio
async def test_create_proxy_rejects_constructed_blank_host_without_writing(tmp_path) -> None:
    """The repository guard runs before its transaction, even for bypassed schemas."""
    configure_database(tmp_path / "telebuba.db")
    poisoned = ProxyCreate.model_construct(proxy_type="socks5", host="   ", port=1080)

    with pytest.raises(ValueError, match="must not be blank"):
        await create_proxy(poisoned)

    assert (await list_proxies()).proxies == []


@pytest.mark.asyncio
async def test_repository_rejects_constructed_host_port_without_writing(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    poisoned = ProxyCreate.model_construct(
        proxy_type="socks5",
        host="example.com:1080",
        port=1080,
    )

    with pytest.raises(ValueError, match="must not include a port"):
        await create_proxy(poisoned)

    assert (await list_proxies()).proxies == []


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
async def test_concurrent_pool_assignments_respect_final_capacity_slot(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    proxy = await create_proxy(ProxyCreate(proxy_type="socks5", host="pool.example", port=1080))
    for account_id in ("first", "second", "third", "fourth"):
        await _account(account_id)
    for account_id in ("first", "second"):
        await assign_account_to_proxy(proxy.id, account_id)

    results = await asyncio.gather(
        assign_account_to_proxy(proxy.id, "third"),
        assign_account_to_proxy(proxy.id, "fourth"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ProxyCapacityError) for result in results) == 1
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    final_proxy = await fetch_proxy(proxy.id)
    assert final_proxy is not None
    assert final_proxy.used == 3


@pytest.mark.asyncio
async def test_assign_by_endpoint_preserves_credentials_on_capacity_failure(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    original = await create_proxy(
        ProxyCreate(
            proxy_type="socks5",
            host="Example.COM.",
            port=1080,
            username="alice",
            password="original",
        ),
    )
    for index in range(3):
        account_id = f"acc-{index}"
        await _account(account_id)
        await assign_account_to_proxy(original.id, account_id)
    await _account("overflow")
    replacement = ProxyCreateAssignment(
        proxy_type="socks5",
        host="example.com",
        port=1080,
        username="mallory",
        password="replacement",
        account_id="overflow",
    )

    with pytest.raises(ProxyCapacityError):
        await create_and_assign_proxy(replacement)
    already_assigned = await create_and_assign_proxy(
        replacement.model_copy(update={"account_id": "acc-0"}),
    )
    assert already_assigned.id == original.id

    settings = await fetch_account_proxy_settings("acc-0")
    assert settings is not None
    assert (settings.username, settings.password) == ("alice", "original")
    assert await fetch_account_proxy_settings("overflow") is None
    assert len((await list_proxies()).proxies) == 1

    await unassign_account_from_proxy("acc-2")
    assigned = await create_and_assign_proxy(replacement)
    assert assigned.id == original.id
    settings = await fetch_account_proxy_settings("overflow")
    assert settings is not None
    assert (settings.username, settings.password) == ("alice", "original")


@pytest.mark.asyncio
async def test_assign_by_endpoint_creates_new_proxy_and_explicit_add_rotates(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await _account("owner")
    assigned = await create_and_assign_proxy(
        ProxyCreateAssignment(
            proxy_type="socks5",
            host="new.example",
            port=1080,
            username="alice",
            password="initial",
            account_id="owner",
        ),
    )
    settings = await fetch_account_proxy_settings("owner")
    assert settings is not None
    assert settings.password == "initial"

    rotated = await create_proxy(
        ProxyCreate(
            proxy_type="socks5",
            host="new.example",
            port=1080,
            username="alice",
            password="rotated",
        ),
    )
    assert rotated.id == assigned.id
    settings = await fetch_account_proxy_settings("owner")
    assert settings is not None
    assert settings.password == "rotated"


@pytest.mark.asyncio
async def test_concurrent_assign_by_endpoint_creates_one_shared_proxy(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await _account("first")
    await _account("second")
    first, second = await asyncio.gather(
        create_and_assign_proxy(
            ProxyCreateAssignment(
                proxy_type="socks5",
                host="same.example",
                port=1080,
                username="first",
                password="first",
                account_id="first",
            ),
        ),
        create_and_assign_proxy(
            ProxyCreateAssignment(
                proxy_type="socks5",
                host="same.example",
                port=1080,
                username="second",
                password="second",
                account_id="second",
            ),
        ),
    )
    assert first.id == second.id
    pool = await list_proxies()
    assert len(pool.proxies) == 1
    assert pool.proxies[0].used == 2
    first_settings = await fetch_account_proxy_settings("first")
    second_settings = await fetch_account_proxy_settings("second")
    assert first_settings is not None
    assert second_settings is not None
    assert first_settings.password == second_settings.password


@pytest.mark.asyncio
async def test_explicit_create_and_endpoint_assignment_serialize_identity(
    tmp_path, monkeypatch
) -> None:
    configure_database(tmp_path / "telebuba.db")
    await _account("owner")
    old_selected = Event()
    release_old = Event()
    new_started = Event()
    new_inserted = Event()
    original_execute = Connection.execute
    original_exec_driver_sql = Connection.exec_driver_sql

    def tracked_execute(connection, statement, *args, **kwargs):
        result = original_execute(connection, statement, *args, **kwargs)
        sql = str(statement)
        if current_thread().name.startswith("explicit-create") and sql.startswith(
            "SELECT proxies.id",
        ):
            old_selected.set()
            if not release_old.wait(5):
                msg = "Explicit create was not released"
                raise TimeoutError(msg)
        if current_thread().name.startswith("atomic-assign") and sql.startswith(
            "INSERT INTO proxies",
        ):
            new_inserted.set()
        return result

    def tracked_exec_driver_sql(connection, statement, *args, **kwargs):
        if current_thread().name.startswith("atomic-assign") and statement == "BEGIN IMMEDIATE":
            new_started.set()
        return original_exec_driver_sql(connection, statement, *args, **kwargs)

    monkeypatch.setattr(Connection, "execute", tracked_execute)
    monkeypatch.setattr(Connection, "exec_driver_sql", tracked_exec_driver_sql)
    old_data = ProxyCreate(
        proxy_type="socks5",
        host="same.example",
        port=1080,
        username="original",
        password="original",
    )
    new_data = ProxyCreateAssignment(
        proxy_type="socks5",
        host="same.example",
        port=1080,
        username="ignored",
        password="ignored",
        account_id="owner",
    )

    with (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="explicit-create") as old_pool,
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="atomic-assign") as new_pool,
    ):
        old_future = old_pool.submit(proxy_repository._create_proxy, old_data)
        try:
            assert old_selected.wait(5)
            new_future = new_pool.submit(proxy_repository._create_and_assign_proxy, new_data)
            assert new_started.wait(5)
            assert not new_inserted.wait(1)
        finally:
            release_old.set()
        created = old_future.result(timeout=5)
        assigned = new_future.result(timeout=5)

    assert created.id == assigned.id
    settings = await fetch_account_proxy_settings("owner")
    assert settings is not None
    assert (settings.username, settings.password) == ("original", "original")


@pytest.mark.asyncio
async def test_concurrent_assign_by_endpoint_respects_final_capacity_slot(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    proxy = await create_proxy(ProxyCreate(proxy_type="socks5", host="full.example", port=1080))
    for account_id in ("first", "second", "third", "fourth"):
        await _account(account_id)
    for account_id in ("first", "second"):
        await assign_account_to_proxy(proxy.id, account_id)

    results = await asyncio.gather(
        *(
            create_and_assign_proxy(
                ProxyCreateAssignment(
                    proxy_type="socks5",
                    host="full.example",
                    port=1080,
                    account_id=account_id,
                ),
            )
            for account_id in ("third", "fourth")
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ProxyCapacityError) for result in results) == 1
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    final_proxy = await fetch_proxy(proxy.id)
    assert final_proxy is not None
    assert final_proxy.used == 3


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
            geo_status="confirmed",
            ipinfo_country_code="NL",
            maxmind_country_code="NL",
            asn="AS1 Hetzner",
            is_datacenter=True,
        ),
    )
    assert saved.status == "tcp_working"
    assert saved.country_code == "NL"
    assert saved.geo_status == "confirmed"
    assert saved.ipinfo_country_code == "NL"
    assert saved.maxmind_country_code == "NL"
    assert saved.is_datacenter is True

    accounts = await list_accounts()
    assert accounts.accounts[0].proxy_country_code == "NL"


@pytest.mark.asyncio
async def test_update_proxy_check_missing_proxy_raises(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    with pytest.raises(ValueError, match="Proxy not found"):
        await update_proxy_check(ProxyCheckUpdate(proxy_id="missing", status="failed"))
