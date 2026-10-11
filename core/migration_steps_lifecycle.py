"""Migration 74 — the account lifecycle journal, backfilled from the ``logs`` table.

``CREATE ... IF NOT EXISTS`` throughout: ``create_all`` runs BEFORE ``apply_migrations``,
so on a fresh database the schema comes from ``core._schema_lifecycle`` and only the
backfill does anything (on an empty ``logs`` table, nothing). The two spellings must
agree down to the CHECK constraint — pinned by ``tests/core/test_migrations_lifecycle.py``.

The backfill reconstructs the journal from whatever the 30-day log retention still
holds. Every reconstructed row is ``backfilled = 1`` and carries the ``logs.id`` it came
from in ``source_log_id``, whose unique index makes a second run insert nothing. What
the logs cannot tell is approximated and stays marked as such:

* ``stint_started_at`` is the latest earlier ``warming_started`` log for the account,
  else the account's live ``warming_account_state.started_at``;
* a deletion carries the guessed reason (``reason_source = 'guess'``): ``dead`` when
  @SpamBot ever answered ``limited`` for the account before it was deleted, else
  ``retired`` — the logs hold no session-check verdicts to add the other signal;
* ``tg_user_id`` comes from the ``accounts`` row only while it still belongs to the same
  account, i.e. no ``account_removed`` was logged for that id at or after the event.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_TABLE = (
    "CREATE TABLE IF NOT EXISTS account_lifecycle_events ("
    " id INTEGER NOT NULL,"
    " account_id VARCHAR NOT NULL,"
    " tg_user_id BIGINT,"
    " stint_started_at VARCHAR,"
    " kind VARCHAR NOT NULL,"
    " created_at VARCHAR NOT NULL,"
    " extra VARCHAR DEFAULT '{}' NOT NULL,"
    " backfilled INTEGER DEFAULT 0 NOT NULL,"
    " source_log_id INTEGER,"
    " PRIMARY KEY (id),"
    " CHECK (kind IN ('warming_started','warmed','promoted','handed_off','quarantined',"
    "'spambot_verdict','session_dead','deleted')))"
)
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_account_lifecycle_events_account"
    " ON account_lifecycle_events (account_id, id)",
    "CREATE INDEX IF NOT EXISTS ix_account_lifecycle_events_kind"
    " ON account_lifecycle_events (kind, created_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_account_lifecycle_events_source_log"
    " ON account_lifecycle_events (source_log_id)",
)

# One literal statement, no string assembly. ``json_extract`` raises on malformed JSON,
# so every read of ``logs.extra`` goes through a CASE (guaranteed lazy, unlike AND)
# that yields NULL for a broken row. ``r`` keeps a re-imported id from lending its new
# row to an older life's events; ``p`` finds the stint start; ``q`` the limited verdict.
_BACKFILL = """
INSERT OR IGNORE INTO account_lifecycle_events
    (account_id, tg_user_id, stint_started_at, kind, created_at, extra,
     backfilled, source_log_id)
SELECT
    l.account_id,
    a.user_id,
    CASE WHEN m.kind = 'warming_started' THEN l.created_at ELSE COALESCE(
        (SELECT p.created_at FROM logs p
          WHERE p.account_id = l.account_id AND p.event = 'warming_started' AND p.id < l.id
          ORDER BY p.id DESC LIMIT 1),
        s.started_at) END,
    m.kind,
    l.created_at,
    CASE m.kind
        WHEN 'spambot_verdict' THEN json_object(
            'status', CASE WHEN json_valid(l.extra) THEN json_extract(l.extra, '$.status') END,
            'detail', CASE WHEN json_valid(l.extra) THEN json_extract(l.extra, '$.detail') END)
        WHEN 'quarantined' THEN json_object('status', 'peer_flood')
        WHEN 'deleted' THEN json_object(
            'reason', CASE WHEN EXISTS (
                SELECT 1 FROM logs q
                 WHERE q.account_id = l.account_id AND q.event = 'spam_status_refreshed'
                   AND q.id < l.id
                   AND CASE WHEN json_valid(q.extra)
                       THEN json_extract(q.extra, '$.status') END = 'limited')
                THEN 'dead' ELSE 'retired' END,
            'reason_source', 'guess')
        ELSE '{}' END,
    1,
    l.id
FROM logs l
JOIN (SELECT 'warming_started' AS event, 'warming_started' AS kind
      UNION ALL SELECT 'warming_target_reached', 'warmed'
      UNION ALL SELECT 'warming_promoted_to_neurocomment', 'promoted'
      UNION ALL SELECT 'warming_nc_handoff', 'handed_off'
      UNION ALL SELECT 'warming_cycle_completed', 'quarantined'
      UNION ALL SELECT 'spam_status_refreshed', 'spambot_verdict'
      UNION ALL SELECT 'account_removed', 'deleted') m ON m.event = l.event
LEFT JOIN accounts a ON a.account_id = l.account_id AND NOT EXISTS (
    SELECT 1 FROM logs r
     WHERE r.account_id = l.account_id AND r.event = 'account_removed' AND r.id >= l.id)
LEFT JOIN warming_account_state s ON s.account_id = l.account_id AND NOT EXISTS (
    SELECT 1 FROM logs r
     WHERE r.account_id = l.account_id AND r.event = 'account_removed' AND r.id >= l.id)
WHERE l.account_id IS NOT NULL
  AND (l.event <> 'warming_cycle_completed'
       OR CASE WHEN json_valid(l.extra)
          THEN json_extract(l.extra, '$.status') END = 'peer_flood')
ORDER BY l.id
"""
_SOURCES_PRESENT = (
    "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
    " AND name IN ('logs', 'accounts', 'warming_account_state')"
)
_SOURCE_COUNT = 3


def _add_account_lifecycle_events(connection: Connection) -> None:
    connection.exec_driver_sql(_TABLE)
    for statement in _INDEXES:
        connection.exec_driver_sql(statement)
    # A hand-built legacy database may predate the tables the backfill reads; there is
    # then nothing to reconstruct.
    if int(connection.exec_driver_sql(_SOURCES_PRESENT).scalar_one()) == _SOURCE_COUNT:
        connection.exec_driver_sql(_BACKFILL)
