"""Per-account limit-override table — a sibling of ``core.migration_steps``.

Its own module because ``core.migration_steps_neurocomment`` is at the file-size cap.
Idempotent, per the append-only migration contract in ``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_neurocomment_account_limits(connection: Connection) -> None:
    """#58: let one account carry its own caps instead of the fleet's.

    Nullable columns, no defaults: a NULL is "this cap follows the fleet", which zero
    cannot say — zero already means "no cap" on the join and per-channel caps. Nothing
    is backfilled, so every existing account keeps reading the fleet numbers until an
    operator saves an override for it.
    """
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neurocomment_account_limits ("
        " account_id VARCHAR NOT NULL PRIMARY KEY,"
        " max_joins_per_day INTEGER,"
        " max_comments_per_hour INTEGER,"
        " max_comments_per_channel_per_day INTEGER,"
        " updated_at VARCHAR NOT NULL)",
    )
