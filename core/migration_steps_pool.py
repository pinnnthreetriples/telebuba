"""Overflow migration bodies — split from ``core.migration_steps`` for size.

Kept in its own module so ``core.migration_steps`` stays under the file-size
budget. Holds the proxy-pool migration (#18 — the shared ``proxies`` table +
``accounts.proxy_id`` FK, backfilled from the retired ``account_proxies``), the
warming ``activity_persona`` column (#21) and the neurocomment
``listener_running`` flag (#24), and proxy geo consensus columns (#32). The
generic SQLite helpers are imported from
``core.migration_steps``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _backfill_proxy_pool(connection: Connection) -> None:
    # Collapse the per-account `account_proxies` rows into shared pool proxies:
    # identical (host, port, type) become one proxy, and each owning account is
    # pointed at it via accounts.proxy_id. Groups that already exceed the global
    # capacity are kept (a migration never detaches an account).
    rows = (
        connection.exec_driver_sql(
            "SELECT account_id, proxy_type, host, port, username, password, status, "
            "last_checked_at, last_error, exit_ip, country_code, country_name, asn, "
            "is_datacenter, created_at, updated_at FROM account_proxies",
        )
        .mappings()
        .all()
    )
    by_identity: dict[tuple[str, int, str], str] = {}
    for row in rows:
        identity = (str(row["host"]), int(row["port"]), str(row["proxy_type"]))
        proxy_id = by_identity.get(identity)
        if proxy_id is None:
            proxy_id = uuid.uuid4().hex
            by_identity[identity] = proxy_id
            connection.exec_driver_sql(
                "INSERT INTO proxies (id, proxy_type, host, port, username, password, "
                "status, last_checked_at, last_error, exit_ip, country_code, country_name, "
                "asn, is_datacenter, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    proxy_id,
                    row["proxy_type"],
                    row["host"],
                    row["port"],
                    row["username"],
                    row["password"],
                    row["status"],
                    row["last_checked_at"],
                    row["last_error"],
                    row["exit_ip"],
                    row["country_code"],
                    row["country_name"],
                    row["asn"],
                    row["is_datacenter"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
        connection.exec_driver_sql(
            "UPDATE accounts SET proxy_id = ? WHERE account_id = ?",
            (proxy_id, row["account_id"]),
        )


def _add_proxy_pool(connection: Connection) -> None:
    # Proxy pool: one shared `proxies` table (the only proxy store) + an
    # accounts.proxy_id FK. Backfills then drops the retired `account_proxies`.
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS proxies ("
        "  id VARCHAR PRIMARY KEY,"
        "  proxy_type VARCHAR NOT NULL,"
        "  host VARCHAR NOT NULL,"
        "  port INTEGER NOT NULL,"
        "  username VARCHAR,"
        "  password VARCHAR,"
        "  status VARCHAR NOT NULL,"
        "  last_checked_at VARCHAR,"
        "  last_error VARCHAR,"
        "  exit_ip VARCHAR,"
        "  country_code VARCHAR,"
        "  country_name VARCHAR,"
        "  asn VARCHAR,"
        "  is_datacenter INTEGER,"
        "  created_at VARCHAR NOT NULL,"
        "  updated_at VARCHAR NOT NULL"
        ")",
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_proxies_identity ON proxies(host, port, proxy_type)",
    )
    if "proxy_id" not in _sqlite_columns(connection, "accounts"):
        connection.exec_driver_sql(
            "ALTER TABLE accounts ADD COLUMN proxy_id VARCHAR REFERENCES proxies(id)",
        )
    if _sqlite_table_exists(connection, "account_proxies"):
        _backfill_proxy_pool(connection)
        connection.exec_driver_sql("DROP TABLE account_proxies")


def _add_neurocomment_listener_running(connection: Connection) -> None:
    # audit 2026-07-02: split "which account is the listener" from "is the runtime
    # subscribed". Without this, pause (the global stop) cleared listener_account_id,
    # so a paused listener was indistinguishable from a removed one after a reload.
    # DEFAULT 0 so a legacy row with a remembered listener_account_id boots PAUSED
    # (never auto-resumes); create_all builds fresh DBs with the same server_default.
    if not _sqlite_table_exists(connection, "neurocomment_runtime"):
        return
    if "listener_running" not in _sqlite_columns(connection, "neurocomment_runtime"):
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_runtime "
            "ADD COLUMN listener_running INTEGER NOT NULL DEFAULT 0",
        )


def _add_warming_state_activity_persona(connection: Connection) -> None:
    # Operator-chosen activity persona (the start modal, beside the day slider).
    # DEFAULT 'normal' backfills existing rows to the balanced cadence; the reader
    # maps any residual NULL up to "normal" too.
    if not _sqlite_table_exists(connection, "warming_account_state"):
        return
    if "activity_persona" not in _sqlite_columns(connection, "warming_account_state"):
        connection.exec_driver_sql(
            "ALTER TABLE warming_account_state ADD COLUMN activity_persona VARCHAR "
            "NOT NULL DEFAULT 'normal'",
        )


def _add_proxy_geo_consensus(connection: Connection) -> None:
    """Add independently persisted provider results for existing proxy pools."""
    if not _sqlite_table_exists(connection, "proxies"):
        return
    columns = _sqlite_columns(connection, "proxies")
    if "geo_status" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE proxies ADD COLUMN geo_status VARCHAR NOT NULL DEFAULT 'unknown'",
        )
    if "ipinfo_country_code" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE proxies ADD COLUMN ipinfo_country_code VARCHAR",
        )
    if "maxmind_country_code" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE proxies ADD COLUMN maxmind_country_code VARCHAR",
        )
