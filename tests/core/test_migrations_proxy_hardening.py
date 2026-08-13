"""Regression tests for the proxy-host integrity migration."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import IntegrityError

from core.db import _get_engine
from core.migration_steps_proxy_hardening import _harden_proxy_hosts

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

    from tests.core.conftest import _EngineFactory


def _create_legacy_tables(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE proxies (id VARCHAR PRIMARY KEY, host VARCHAR NOT NULL)",
    )
    connection.exec_driver_sql(
        "CREATE TABLE accounts (account_id VARCHAR PRIMARY KEY, proxy_id VARCHAR)",
    )


def test_proxy_host_migration_is_stamped_after_inbox() -> None:
    with _get_engine().connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT version, name FROM schema_version WHERE version IN (53, 54) ORDER BY version",
        ).all()
    assert rows == [(53, "add_neurocomment_inbox"), (54, "harden_proxy_hosts")]


def test_proxy_host_migration_detaches_and_deletes_blank_legacy_rows(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("blank-proxy.db")
    with engine.begin() as connection:
        _create_legacy_tables(connection)
        connection.exec_driver_sql("INSERT INTO proxies(id, host) VALUES ('bad', '   ')")
        connection.exec_driver_sql("INSERT INTO proxies(id, host) VALUES ('good', 'proxy.test')")
        connection.exec_driver_sql("INSERT INTO accounts(account_id, proxy_id) VALUES ('a', 'bad')")

        _harden_proxy_hosts(connection)

        assert connection.exec_driver_sql("SELECT id FROM proxies ORDER BY id").all() == [
            ("good",),
        ]
        assert (
            connection.exec_driver_sql(
                "SELECT proxy_id FROM accounts WHERE account_id = 'a'",
            ).scalar_one()
            is None
        )


def test_proxy_host_migration_enforces_insert_update_and_is_idempotent(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("proxy-trigger.db")
    with engine.begin() as connection:
        _create_legacy_tables(connection)
        connection.exec_driver_sql("INSERT INTO proxies(id, host) VALUES ('good', 'proxy.test')")
        _harden_proxy_hosts(connection)
        _harden_proxy_hosts(connection)

    with engine.begin() as connection, pytest.raises(IntegrityError, match="must not be blank"):
        connection.exec_driver_sql("INSERT INTO proxies(id, host) VALUES ('bad', '')")
    with engine.begin() as connection, pytest.raises(IntegrityError, match="must not be blank"):
        connection.exec_driver_sql("UPDATE proxies SET host = '  ' WHERE id = 'good'")
