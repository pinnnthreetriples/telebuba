"""Migrations 85-87 — the account behaves like a person (Прогрев 2.0, stage 4).

The real Telegram registration date on ``accounts`` (85), each account's character
(86), and the visit the cycle in flight runs, for the card's progress (87). NULL /
no row everywhere is the old behaviour. Idempotent, per the contract in
``core.migrations``; ``create_all`` builds a fresh database from the table modules and
``tests/core/test_migrations_humanlike.py`` keeps both spellings equal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_columns(connection: Connection, table: str, columns: dict[str, str]) -> None:
    if not _sqlite_table_exists(connection, table):
        return
    present = _sqlite_columns(connection, table)
    for name, sql_type in columns.items():
        if name not in present:
            connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")


def _add_account_registered_at(connection: Connection) -> None:
    _add_columns(
        connection,
        "accounts",
        {"telegram_registered_at": "VARCHAR", "registered_source": "VARCHAR"},
    )


def _add_warming_account_character(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS warming_account_character ("
        " account_id VARCHAR NOT NULL,"
        " language VARCHAR NOT NULL,"
        " country VARCHAR,"
        " age INTEGER NOT NULL,"
        " gender VARCHAR NOT NULL,"
        " city VARCHAR,"
        " occupation VARCHAR NOT NULL,"
        " interests VARCHAR NOT NULL,"
        " created_at VARCHAR NOT NULL,"
        " PRIMARY KEY (account_id))",
    )


def _add_warming_state_visit(connection: Connection) -> None:
    _add_columns(
        connection,
        "warming_account_state",
        {"visit_kind": "VARCHAR", "cycle_plan": "VARCHAR"},
    )
