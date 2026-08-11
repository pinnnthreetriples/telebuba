"""Proxy-host integrity migration.

The API and repository reject blank hosts, while these database triggers keep
the same invariant for legacy scripts and future internal write paths.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _harden_proxy_hosts(connection: Connection) -> None:
    """Remove unusable legacy rows and enforce non-blank proxy hosts."""
    if not _sqlite_table_exists(connection, "proxies"):
        return
    if "host" not in _sqlite_columns(connection, "proxies"):
        return

    if _sqlite_table_exists(connection, "accounts") and "proxy_id" in _sqlite_columns(
        connection,
        "accounts",
    ):
        connection.exec_driver_sql(
            "UPDATE accounts SET proxy_id = NULL "
            "WHERE proxy_id IN (SELECT id FROM proxies WHERE length(trim(host)) = 0)",
        )
    connection.exec_driver_sql("DELETE FROM proxies WHERE length(trim(host)) = 0")
    connection.exec_driver_sql(
        "CREATE TRIGGER IF NOT EXISTS proxies_host_non_blank_insert "
        "BEFORE INSERT ON proxies "
        "WHEN length(trim(NEW.host)) = 0 "
        "BEGIN SELECT RAISE(ABORT, 'proxy host must not be blank'); END",
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER IF NOT EXISTS proxies_host_non_blank_update "
        "BEFORE UPDATE OF host ON proxies "
        "WHEN length(trim(NEW.host)) = 0 "
        "BEGIN SELECT RAISE(ABORT, 'proxy host must not be blank'); END",
    )
