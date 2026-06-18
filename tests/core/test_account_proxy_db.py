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
async def test_upsert_account_proxy_preserves_check_state_when_identity_unchanged(
    tmp_path,
) -> None:
    """Re-save of same proxy preserves the prior connectivity-check fields.

    User hits Save after a successful check — status / exit_ip / country must
    survive instead of resetting to "unknown".
    """
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreate(account_id="acc-keep"))
    await upsert_account_proxy(
        AccountProxyUpsertFactory.build(account_id="acc-keep", host="1.2.3.4", port=9050),
    )
    await update_account_proxy_check(
        AccountProxyCheckUpdate(
            account_id="acc-keep",
            status="tcp_working",
            exit_ip="45.130.253.155",
            country_code="NL",
            country_name="Netherlands",
            asn="AS24940 Hetzner",
            is_datacenter=True,
        ),
    )

    re_saved = await upsert_account_proxy(
        AccountProxyUpsertFactory.build(account_id="acc-keep", host="1.2.3.4", port=9050),
    )

    assert re_saved.status == "tcp_working"
    assert re_saved.exit_ip == "45.130.253.155"
    assert re_saved.country_code == "NL"
    assert re_saved.country_name == "Netherlands"
    assert re_saved.asn == "AS24940 Hetzner"
    assert re_saved.is_datacenter is True
    assert re_saved.last_checked_at is not None


@pytest.mark.asyncio
async def test_upsert_account_proxy_preserves_check_state_when_password_unchanged(
    tmp_path,
) -> None:
    """Re-save with a pre-filled password (same value) must not reset check state.

    Dialog now pre-fills the password, so the caller sends the literal current
    password on every save. The repo must treat equal passwords as "unchanged".
    """
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreate(account_id="acc-pwd"))
    await upsert_account_proxy(
        AccountProxyUpsertFactory.build(
            account_id="acc-pwd",
            host="1.2.3.4",
            port=9050,
            username="alice",
            password="pwd",  # noqa: S106 - test fixture.
        ),
    )
    await update_account_proxy_check(
        AccountProxyCheckUpdate(account_id="acc-pwd", status="tcp_working", country_code="NL"),
    )

    re_saved = await upsert_account_proxy(
        AccountProxyUpsertFactory.build(
            account_id="acc-pwd",
            host="1.2.3.4",
            port=9050,
            username="alice",
            password="pwd",  # noqa: S106 - test fixture.
        ),
    )

    assert re_saved.status == "tcp_working"
    assert re_saved.country_code == "NL"
    assert re_saved.has_password is True


@pytest.mark.asyncio
async def test_upsert_account_proxy_clears_stored_password_when_field_emptied(
    tmp_path,
) -> None:
    """Clearing the dialog password field actually removes the stored password.

    Previously, password=None on update meant "leave unchanged" — but with the
    pre-fill UX that magic is gone: None means the user wants no password.
    """
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreate(account_id="acc-clear"))
    await upsert_account_proxy(
        AccountProxyUpsertFactory.build(
            account_id="acc-clear",
            password="pwd",  # noqa: S106 - test fixture.
        ),
    )

    re_saved = await upsert_account_proxy(
        AccountProxyUpsertFactory.build(account_id="acc-clear", password=None),
    )

    assert re_saved.has_password is False
    assert re_saved.status == "unknown"


@pytest.mark.asyncio
async def test_upsert_account_proxy_resets_check_state_on_identity_change(tmp_path) -> None:
    """Changing host/port/type/username invalidates the prior check — fields must clear."""
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreate(account_id="acc-change"))
    await upsert_account_proxy(
        AccountProxyUpsertFactory.build(account_id="acc-change", host="1.2.3.4", port=9050),
    )
    await update_account_proxy_check(
        AccountProxyCheckUpdate(
            account_id="acc-change",
            status="tcp_working",
            exit_ip="45.130.253.155",
            country_code="NL",
            country_name="Netherlands",
        ),
    )

    re_saved = await upsert_account_proxy(
        AccountProxyUpsertFactory.build(account_id="acc-change", host="9.9.9.9", port=9050),
    )

    assert re_saved.status == "unknown"
    assert re_saved.exit_ip is None
    assert re_saved.country_code is None
    assert re_saved.country_name is None
    assert re_saved.last_checked_at is None


@pytest.mark.asyncio
async def test_delete_account_proxy_removes_settings(tmp_path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreate(account_id="acc-2"))
    await upsert_account_proxy(
        AccountProxyUpsertFactory.build(
            account_id="acc-2",
            proxy_type="https",
            host="proxy.local",
            port=8080,
        ),
    )

    await delete_account_proxy(AccountProxyDelete(account_id="acc-2"))

    assert await fetch_account_proxy("acc-2") is None
    assert await fetch_account_proxy_settings("acc-2") is None
