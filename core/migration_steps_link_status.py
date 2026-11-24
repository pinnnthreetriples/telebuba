"""Migration 124 — the link state beside the key state (``accounts.link_status``).

``status`` says what Telegram answered about the KEY; ``link_status`` says the last check
could not reach Telegram at all (flood wait, network, proxy, unknown), so the key state
stayed as it was. ``link_checked_at`` is when that last check ran. Both nullable and
additive; no backfill (a legacy row keeps the transport value it has in ``status`` until a
real verdict arrives). Idempotent, per the append-only contract in ``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_account_link_status(connection: Connection) -> None:
    if not _sqlite_table_exists(connection, "accounts"):
        return
    columns = _sqlite_columns(connection, "accounts")
    for name in ("link_status", "link_checked_at"):
        if name not in columns:
            connection.exec_driver_sql(f"ALTER TABLE accounts ADD COLUMN {name} VARCHAR")
