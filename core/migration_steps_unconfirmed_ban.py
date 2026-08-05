"""Repeated-unconfirmed-ban migration body — a sibling of ``core.migration_steps_rejoin``.

Its own module for the reason those have: the neurocomment migration modules are at the
file-size cap. Idempotent, per the append-only migration contract in ``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_readiness_unconfirmed_ban(connection: Connection) -> None:
    # #47: ``UserBannedInChannelError`` that the per-group ladder could NOT confirm used to
    # cost nothing but a cooldown, so the same pair was re-selected on the channel's next
    # post and refused again — live DB: one account, one channel, four times running, ten
    # failures and zero comments over three days. These two columns are the budget that
    # ends it: how many unconfirmed refusals this pair has collected here, and when the
    # last one landed, so a count older than the window starts over. NOT the #43 re-join
    # pair — that one counts attempts to get back INTO a chat we were thrown out of, and
    # this pair is still a member. NULL / 0 on existing rows = nothing collected yet, which
    # is the right starting point: nobody can be banned on evidence gathered before the
    # column existed.
    columns = _sqlite_columns(connection, "neurocomment_readiness")
    if "unconfirmed_bans" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_readiness "
            "ADD COLUMN unconfirmed_bans INTEGER NOT NULL DEFAULT 0",
        )
    if "unconfirmed_ban_at" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_readiness ADD COLUMN unconfirmed_ban_at VARCHAR",
        )
