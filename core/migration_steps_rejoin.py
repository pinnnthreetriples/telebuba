"""Access-loss re-join migration body — a sibling of ``core.migration_steps``.

Its own module because ``core.migration_steps_neurocomment`` is at the file-size cap,
exactly like ``core.migration_steps_channel_pause``. Idempotent, per the append-only
migration contract in ``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_readiness_rejoin(connection: Connection) -> None:
    # #43: a pair kicked out of a discussion group carries the hard-join-failure sentinel
    # (joined=0, captcha_passed=1, ready=0) and nothing ever retried it — onboarding has no
    # timer, so it waited for an operator or a restart. These two columns are what bounds
    # the automatic retry: when the last re-join went out, and how many have. They are NOT
    # the migration #41 join-request pair: that one means "an admin has not pressed Approve
    # yet" and is read by guards that would silently swallow a re-join (``_join_request_in_
    # flight`` holds the pair back forever at its cap, and the sweep would drop the channel
    # as unapproved). NULL / 0 on existing rows = never retried, so the first sweep after
    # the upgrade retries every parked pair once.
    columns = _sqlite_columns(connection, "neurocomment_readiness")
    if "rejoin_attempted_at" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_readiness ADD COLUMN rejoin_attempted_at VARCHAR",
        )
    if "rejoin_attempts" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_readiness "
            "ADD COLUMN rejoin_attempts INTEGER NOT NULL DEFAULT 0",
        )
