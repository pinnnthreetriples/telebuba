"""Join-log access-loss migration body — a sibling of ``core.migration_steps_rejoin``.

Its own module for the same reason those have: the neurocomment migration modules are at
the file-size cap. Idempotent, per the append-only migration contract in
``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_neurocomment_join_log_lost_at(connection: Connection) -> None:
    # #45: when the listener is proven out of a watch channel, the join-log row is stamped
    # here rather than removed. The row is the only record that a ``JoinChannel`` RPC was
    # spent, so the rolling-24h anti-freeze cap must go on counting it — a count that can
    # fall is a cap a looping channel never reaches, because it spends exactly one join per
    # pass. The lost rows of one (account, channel) are also its re-join attempt counter
    # (``listener_rejoin_max_attempts``), which is what makes that budget survive a restart.
    # Raw PRAGMA, like #40 on this same table: ``_sqlite_columns`` guards its table names
    # against a whitelist that this one is not on.
    rows = connection.exec_driver_sql("PRAGMA table_info(neurocomment_join_log)").mappings().all()
    if "lost_at" not in {str(row["name"]) for row in rows}:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_join_log ADD COLUMN lost_at VARCHAR",
        )
