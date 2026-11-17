"""Migration 115 — ``device_fingerprints.api_id``: the API a session's key was created with.

``NULL`` for every existing row: those sessions keep the configured ``TELEGRAM__API_ID`` /
``TELEGRAM__MOBILE_API_ID`` they were made with. A key is never connected with another API
(see ``core.telegram_client._api_credentials``). ``create_all`` builds a fresh database from
``core._schema_tables``; the two spellings must agree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_device_fingerprint_api_id(connection: Connection) -> None:
    if not _sqlite_table_exists(connection, "device_fingerprints"):
        return
    if "api_id" not in _sqlite_columns(connection, "device_fingerprints"):
        connection.exec_driver_sql("ALTER TABLE device_fingerprints ADD COLUMN api_id INTEGER")
