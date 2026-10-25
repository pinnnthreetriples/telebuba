"""Migration 101 — the independent emulators' table.

``CREATE TABLE IF NOT EXISTS``: ``create_all`` runs BEFORE ``apply_migrations``, so on
a fresh database the table comes from ``core._schema_standalone_emulators`` and this
is a no-op. The two spellings must agree — pinned by
``tests/core/test_migrations_standalone_emulators.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
