"""Migration 120 — give back the @SpamBot answers that passive decisions overwrote.

An earlier build, with ``SPAM_INTEL__PASSIVE_DECISIONS=true``, wrote the fold's decision into the
legacy columns of ``account_spam_status`` (``status`` / ``detail`` / ``checked_at``), over what
@SpamBot had said (audit F-006). No code path writes those details any more.

A row is repaired only when its origin is PROVEN: ``(status, detail)`` is exactly one of the pairs
the mirror wrote (an evidence kind that is not a @SpamBot answer, ``frozen``, ``limit_expired``).
@SpamBot's own replies never carry those details (the parser has no such output), and masstagging's
own write says ``peer_flood``. The answer it replaces is read back from the lifecycle journal
(``spambot_verdict`` rows, written only for real @SpamBot answers, newest first); a row with no
journal entry to restore from is LEFT as it is (its true answer is not known), and so is every row
whose journal entry cannot be read. The projection columns are never touched.

Idempotent: a restored row no longer matches. A database without either table is left alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

# What the mirror wrote (``services.spam_intel._evidence`` of that build), as (status, details).
_MIRRORED = (
    ("limited", ("peer_flood_send", "peer_flood_other", "user_banned_in_channel")),
    ("unknown", ("frozen", "limit_expired")),
)

_LATEST = (
    "SELECT {column} FROM account_lifecycle_events e"
    " WHERE e.account_id = account_spam_status.account_id AND e.kind = 'spambot_verdict'"
    " AND json_valid(e.extra)"
    " AND json_extract(e.extra, '$.status') IN ('clean', 'limited', 'unknown')"
    " ORDER BY e.id DESC LIMIT 1"
)


def _latest(column: str) -> str:
    return _LATEST.format(column=column)


def _fingerprint() -> str:
    pairs = " OR ".join(
        "(status = '{}' AND detail IN ({}))".format(
            status, ", ".join(f"'{detail}'" for detail in details)
        )
        for status, details in _MIRRORED
    )
    return f"checked_at <> '' AND ({pairs})"


def _add_spam_mirror_repair(connection: Connection) -> None:
    if not _sqlite_table_exists(connection, "account_spam_status") or not _sqlite_table_exists(
        connection, "account_lifecycle_events"
    ):
        return
    status = _latest("json_extract(e.extra, '$.status')")
    detail = _latest("json_extract(e.extra, '$.detail')")
    checked = _latest("e.created_at")
    connection.exec_driver_sql(
        "UPDATE account_spam_status SET"  # noqa: S608 # nosec B608 - constants of this module only
        f" status = ({status}), detail = ({detail}), checked_at = ({checked})"
        f" WHERE {_fingerprint()} AND EXISTS ({_latest('1')})"
    )
