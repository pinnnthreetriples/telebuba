"""Migrations 82-84 — the warming operator console (Прогрев 2.0, stage 2).

Three nullable columns on ``warming_account_state``, one migration each so every item
of the stage stays separable: the geo consent (82), the settled-actions base of the
cycle in flight (83), and how the account entered neurocomment (84). NULL everywhere
is the old behaviour. Idempotent, per the contract in ``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_TABLE = "warming_account_state"


def _add_columns(connection: Connection, columns: dict[str, str]) -> None:
    if not _sqlite_table_exists(connection, _TABLE):
        return
    present = _sqlite_columns(connection, _TABLE)
    for name, sql_type in columns.items():
        if name not in present:
            connection.exec_driver_sql(f"ALTER TABLE {_TABLE} ADD COLUMN {name} {sql_type}")


def _add_warming_state_geo_ack(connection: Connection) -> None:
    _add_columns(connection, {"geo_ack": "VARCHAR"})


def _add_warming_state_cycle_base(connection: Connection) -> None:
    _add_columns(connection, {"cycle_base_actions": "INTEGER"})


def _add_warming_state_nc_start(connection: Connection) -> None:
    _add_columns(connection, {"nc_start_mode": "VARCHAR", "nc_handoff_at": "VARCHAR"})
