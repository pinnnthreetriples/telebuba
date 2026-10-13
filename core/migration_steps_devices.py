"""Migration #88: an account may hold two authorizations, one per device type.

Every existing account becomes exactly what it already was — one desktop device —
and nothing it owns changes: its ``.session`` file is not touched, and its
fingerprint row is copied column for column under ``device_type = 'desktop'``.

``device_fingerprints`` needs a table rebuild because SQLite cannot change a
primary key in place: ``(account_id)`` becomes ``(account_id, device_type)`` so the
second authorization gets its own row. The rest is plain ``ADD COLUMN``s whose
defaults describe the pre-migration world.

Atomic because ``apply_migrations`` runs every pending step in one transaction, and
idempotent because each part checks the shape it is about to create.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_FINGERPRINT_COLUMNS = (
    "account_id, platform, device_model, system_version, app_version, lang_code, system_lang_code"
)


def _fingerprint_primary_key(connection: Connection) -> set[str]:
    rows = connection.exec_driver_sql("PRAGMA table_info(device_fingerprints)").all()
    return {str(row[1]) for row in rows if int(row[5]) > 0}


def _rebuild_device_fingerprints(connection: Connection) -> None:
    if not _sqlite_table_exists(connection, "device_fingerprints"):
        return
    if _fingerprint_primary_key(connection) == {"account_id", "device_type"}:
        return
    connection.exec_driver_sql(
        "CREATE TABLE device_fingerprints_v88 ("
        "  account_id VARCHAR NOT NULL,"
        "  device_type VARCHAR NOT NULL DEFAULT 'desktop',"
        "  platform VARCHAR NOT NULL,"
        "  device_model VARCHAR NOT NULL,"
        "  system_version VARCHAR NOT NULL,"
        "  app_version VARCHAR NOT NULL,"
        "  lang_code VARCHAR NOT NULL,"
        "  system_lang_code VARCHAR NOT NULL,"
        "  PRIMARY KEY (account_id, device_type)"
        ")",
    )
    connection.exec_driver_sql(
        f"INSERT INTO device_fingerprints_v88 (device_type, {_FINGERPRINT_COLUMNS}) "  # noqa: S608 # nosec B608 - fixed column list
        f"SELECT 'desktop', {_FINGERPRINT_COLUMNS} FROM device_fingerprints",
    )
    connection.exec_driver_sql("DROP TABLE device_fingerprints")
    connection.exec_driver_sql("ALTER TABLE device_fingerprints_v88 RENAME TO device_fingerprints")


def _add_account_device_columns(connection: Connection) -> None:
    if not _sqlite_table_exists(connection, "accounts"):
        return
    columns = _sqlite_columns(connection, "accounts")
    if "session_device" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE accounts ADD COLUMN session_device VARCHAR NOT NULL DEFAULT 'desktop'",
        )
    if "second_session_name" not in columns:
        connection.exec_driver_sql("ALTER TABLE accounts ADD COLUMN second_session_name VARCHAR")
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_accounts_second_session_name_unique "
        "ON accounts (second_session_name)",
    )


def _add_warming_device_mode(connection: Connection) -> None:
    if not _sqlite_table_exists(connection, "warming_account_state"):
        return
    if "device_mode" not in _sqlite_columns(connection, "warming_account_state"):
        connection.exec_driver_sql(
            "ALTER TABLE warming_account_state ADD COLUMN device_mode VARCHAR "
            "NOT NULL DEFAULT 'desktop'",
        )


def _add_account_devices(connection: Connection) -> None:
    _rebuild_device_fingerprints(connection)
    _add_account_device_columns(connection)
    _add_warming_device_mode(connection)
