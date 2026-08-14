"""Regression tests for the proxy-host integrity migration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import IntegrityError

from core.db import _get_engine
from core.migration_steps_proxy_hardening import _harden_proxy_hosts

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

    from tests.core.conftest import _EngineFactory

_MIGRATION_LOGGER = "core.migration_steps_proxy_hardening"


def _create_legacy_tables(connection: Connection) -> None:
    """The post-#18 pool shape: identity is (host, port, proxy_type) and is unique."""
    connection.exec_driver_sql(
        "CREATE TABLE proxies (id VARCHAR PRIMARY KEY, proxy_type VARCHAR NOT NULL,"
        " host VARCHAR NOT NULL, port INTEGER NOT NULL)",
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX ix_proxies_identity ON proxies(host, port, proxy_type)",
    )
    connection.exec_driver_sql(
        "CREATE TABLE accounts (account_id VARCHAR PRIMARY KEY, proxy_id VARCHAR)",
    )


def _insert_proxy(connection: Connection, proxy_id: str, host: str, port: int = 1080) -> None:
    connection.exec_driver_sql(
        "INSERT INTO proxies(id, proxy_type, host, port) VALUES (?, 'socks5', ?, ?)",
        (proxy_id, host, port),
    )


def _hosts(connection: Connection) -> list[tuple[str, str]]:
    return [
        (str(row[0]), str(row[1]))
        for row in connection.exec_driver_sql("SELECT id, host FROM proxies ORDER BY id").all()
    ]


def test_proxy_host_migration_is_stamped_after_inbox() -> None:
    with _get_engine().connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT version, name FROM schema_version WHERE version IN (53, 54) ORDER BY version",
        ).all()
    assert rows == [(53, "add_neurocomment_inbox"), (54, "harden_proxy_hosts")]


def test_proxy_host_migration_detaches_and_deletes_blank_legacy_rows(
    legacy_engine: _EngineFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = legacy_engine("blank-proxy.db")
    with engine.begin() as connection:
        _create_legacy_tables(connection)
        _insert_proxy(connection, "bad", "   ")
        _insert_proxy(connection, "good", "proxy.test")
        connection.exec_driver_sql("INSERT INTO accounts(account_id, proxy_id) VALUES ('a', 'bad')")

        with caplog.at_level(logging.WARNING, logger=_MIGRATION_LOGGER):
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
    # The operator has to re-add the endpoint and re-assign the account by hand, so
    # the log is the only place either loss is ever recorded.
    assert "deleted 1 proxy row(s) with a blank host" in caplog.text
    assert "Deleted proxy ids: bad" in caplog.text
    assert "re-assign a proxy to each): a" in caplog.text


def test_proxy_host_migration_reports_nothing_when_there_is_nothing_to_report(
    legacy_engine: _EngineFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A clean pool upgrades in silence — a warning would train operators to ignore it."""
    engine = legacy_engine("quiet-proxy.db")
    with engine.begin() as connection:
        _create_legacy_tables(connection)
        _insert_proxy(connection, "good", "proxy.test")

        with caplog.at_level(logging.WARNING, logger=_MIGRATION_LOGGER):
            _harden_proxy_hosts(connection)
            _harden_proxy_hosts(connection)

    assert "migration 54" not in caplog.text


def test_proxy_host_migration_canonicalises_surviving_hosts(
    legacy_engine: _EngineFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A legacy spelling must fold onto the identity the API now sends.

    Otherwise re-adding the same endpoint mints a second pool card instead of
    refreshing the credentials of the one already there.
    """
    engine = legacy_engine("canonical-proxy.db")
    with engine.begin() as connection:
        _create_legacy_tables(connection)
        _insert_proxy(connection, "loud", "PROXY.Example.COM.")
        _insert_proxy(connection, "six", "[2001:0DB8::1]")
        _insert_proxy(connection, "plain", "proxy.test")

        with caplog.at_level(logging.WARNING, logger=_MIGRATION_LOGGER):
            _harden_proxy_hosts(connection)
            _harden_proxy_hosts(connection)

        assert _hosts(connection) == [
            ("loud", "proxy.example.com"),
            ("plain", "proxy.test"),
            ("six", "2001:db8::1"),
        ]
    assert "PROXY.Example.COM. -> proxy.example.com" in caplog.text
    # Second pass: everything is canonical already, so it says it once, not twice.
    assert caplog.text.count("canonicalized") == 1


def test_proxy_host_migration_leaves_colliding_spellings_for_the_operator(
    legacy_engine: _EngineFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two spellings of one endpoint: keep both rows rather than move assignments."""
    engine = legacy_engine("collide-proxy.db")
    with engine.begin() as connection:
        _create_legacy_tables(connection)
        _insert_proxy(connection, "aaa", "proxy.example.com")
        _insert_proxy(connection, "bbb", "PROXY.EXAMPLE.COM")
        # Same spelling, different port — a distinct endpoint, so it still folds.
        _insert_proxy(connection, "ccc", "PROXY.EXAMPLE.COM", port=1081)

        with caplog.at_level(logging.WARNING, logger=_MIGRATION_LOGGER):
            _harden_proxy_hosts(connection)

        assert _hosts(connection) == [
            ("aaa", "proxy.example.com"),
            ("bbb", "PROXY.EXAMPLE.COM"),
            ("ccc", "proxy.example.com"),
        ]
    assert "already exists in the pool as 'proxy.example.com'" in caplog.text


def test_proxy_host_migration_keeps_an_unparseable_host_and_says_so(
    legacy_engine: _EngineFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A host the canonicalizer cannot read is reported, never dropped or guessed at."""
    engine = legacy_engine("unparseable-proxy.db")
    with engine.begin() as connection:
        _create_legacy_tables(connection)
        _insert_proxy(connection, "ported", "proxy.example.com:1080")

        with caplog.at_level(logging.WARNING, logger=_MIGRATION_LOGGER):
            _harden_proxy_hosts(connection)

        assert _hosts(connection) == [("ported", "proxy.example.com:1080")]
    assert "left proxy ported host 'proxy.example.com:1080' as it is" in caplog.text


def test_proxy_host_migration_enforces_insert_update_and_is_idempotent(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("proxy-trigger.db")
    with engine.begin() as connection:
        _create_legacy_tables(connection)
        _insert_proxy(connection, "good", "proxy.test")
        _harden_proxy_hosts(connection)
        _harden_proxy_hosts(connection)

    with engine.begin() as connection, pytest.raises(IntegrityError, match="must not be blank"):
        _insert_proxy(connection, "bad", "")
    with engine.begin() as connection, pytest.raises(IntegrityError, match="must not be blank"):
        connection.exec_driver_sql("UPDATE proxies SET host = '  ' WHERE id = 'good'")
