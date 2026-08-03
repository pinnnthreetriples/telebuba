"""Access-loss reason migration body — a sibling of ``core.migration_steps_rejoin``.

Its own module for the same reason that one has: the neurocomment migration modules are
at the file-size cap. Idempotent, per the append-only migration contract in
``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_readiness_access_lost_reason(connection: Connection) -> None:
    # #44: the sentinel a pair out of the chat carries (joined=0, captcha_passed=1,
    # ready=0) says the pair needs a fresh join but never said WHY it needs one, so the
    # re-join rule spent its whole four-day budget on a kick and on a handle Telegram says
    # nobody owns alike. This column records the Telegram verdict that parked the pair —
    # the error class itself, not a paraphrase of it — beside the sentinel, which stays
    # spelled exactly as it was (``_rejoin.access_lost`` and ``_readiness._ACCESS_LOST``
    # both key on it). NULL on every existing row, and NULL means UNKNOWN, never hopeless:
    # ``_rejoin`` retries those exactly as it did before this column existed.
    columns = _sqlite_columns(connection, "neurocomment_readiness")
    if "access_lost_reason" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_readiness ADD COLUMN access_lost_reason VARCHAR",
        )
