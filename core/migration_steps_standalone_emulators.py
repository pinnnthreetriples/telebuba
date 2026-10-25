"""Migrations 101 and 102 — the independent emulators' table, then the apps a start installs.

``CREATE TABLE IF NOT EXISTS``: ``create_all`` runs BEFORE ``apply_migrations``, so on
a fresh database the table comes from ``core._schema_standalone_emulators`` and this
is a no-op. The two spellings must agree — pinned by
``tests/core/test_migrations_standalone_emulators.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_STANDALONE_EMULATORS = (
    "CREATE TABLE IF NOT EXISTS standalone_emulators ("
    " id VARCHAR NOT NULL,"
    " label VARCHAR,"
    " proxy_id VARCHAR,"
    " device_model VARCHAR NOT NULL,"
    " system_version VARCHAR NOT NULL,"
    " app_version VARCHAR NOT NULL,"
    " lang_code VARCHAR NOT NULL,"
    " system_lang_code VARCHAR NOT NULL,"
    " created_at VARCHAR NOT NULL,"
    " PRIMARY KEY (id),"
    " FOREIGN KEY(proxy_id) REFERENCES proxies (id))"
)


def _add_standalone_emulators(connection: Connection) -> None:
    connection.exec_driver_sql(_STANDALONE_EMULATORS)


def _add_standalone_install_apps(connection: Connection) -> None:
    # Comma-joined ``StandaloneApp`` names. Every phone made before it installed
    # Telegram on its start, so that is what an existing row keeps.
    if "install_apps" not in _sqlite_columns(connection, "standalone_emulators"):
        connection.exec_driver_sql(
            "ALTER TABLE standalone_emulators"
            " ADD COLUMN install_apps VARCHAR NOT NULL DEFAULT 'telegram'",
        )
