"""Neuroshilling migration 56 — the observed-chat log the poller writes.

Its own module rather than a seventh helper in
``core.migration_steps_neuroshilling``: migrations are append-only, and a body
already stamped on every deployed database must not gain statements no stamped
database will ever run.

A dispatcher over one helper per statement group, the same shape migration 55
uses, so no body approaches the function-size budget.

Everything is ``CREATE ... IF NOT EXISTS``: ``core.db._build_engine`` runs
``create_all`` BEFORE ``apply_migrations``, so on a fresh database the real
schema comes from ``core.repositories.neuroshilling._tables`` and every statement
here is a no-op. The two spellings must therefore agree down to the foreign key
and the indexes — pinned by ``tests/core/test_migrations_neuroshilling_chat.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_neuroshilling_chat_log(connection: Connection) -> None:
    """Create ``neuroshilling_chat_log`` and its two indexes."""
    _ns_chat_log(connection)
    _ns_chat_log_indexes(connection)


def _ns_chat_log(connection: Connection) -> None:
    """Every message the poller has SEEN in a target chat, ours and other people's.

    This is the one table in the domain whose ``text`` is written by strangers.
    Nothing reads it as an instruction: it reaches a model only through
    ``services.neuroshilling._prompt``, fenced and trimmed, and whatever the model
    answers is re-checked by a deterministic gate before anything is published.

    ``is_ours`` is wider than Telethon's ``out`` flag. A sibling account of the same
    campaign posting in the same chat is INCOMING to the account doing the reading,
    so the poller widens the answer with the send journal, with the account ids of
    the campaign's own roster, and with the rows the autoreply path writes here for
    its own published answers — which have no journal row, the journal being keyed on
    a scenario step. Without all three the fleet would quote itself back into its own
    context and answer its own lines.

    ``replied`` is a DECISION and not an outcome: it is set the moment a message is
    picked for an answer, whatever becomes of that answer, so a refused or failed
    reply is never retried against the same message. ``replied_at`` and
    ``reply_account_id`` are the outcome, and they are what the reply quota counts —
    an autoreply is not a scenario step, so it has no row in
    ``neuroshilling_messages`` to be counted there.

    ``sender_id`` is INTEGER because SQLite's INTEGER is already 64-bit; a BIGINT
    spelling would only be a second name for the same affinity, and the two schema
    spellings have to match exactly.
    """
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neuroshilling_chat_log ("
        " id INTEGER NOT NULL PRIMARY KEY,"
        " campaign_id VARCHAR NOT NULL"
        " REFERENCES neuroshilling_campaigns(campaign_id) ON DELETE CASCADE,"
        " target VARCHAR NOT NULL,"
        " message_id INTEGER NOT NULL,"
        " sender_id INTEGER,"
        " text VARCHAR NOT NULL DEFAULT '',"
        " is_ours INTEGER NOT NULL DEFAULT 0,"
        " replied INTEGER NOT NULL DEFAULT 0,"
        " reply_account_id VARCHAR,"
        " replied_at VARCHAR,"
        " seen_at VARCHAR NOT NULL)",
    )


def _ns_chat_log_indexes(connection: Connection) -> None:
    """The uniqueness that makes a re-poll idempotent, plus the reply-quota lookup.

    ``ux_ns_chat_log_msg`` is what stops a second poll over an overlapping window
    recording the same message twice — and it doubles as the poll CURSOR, since
    ``MAX(message_id)`` for a (campaign, target) is a prefix scan of it. There is
    deliberately no separate cursor table.
    """
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_ns_chat_log_msg "
        "ON neuroshilling_chat_log(campaign_id, target, message_id)",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_ns_chat_log_reply "
        "ON neuroshilling_chat_log(reply_account_id, replied_at)",
    )
