"""Migrations 68, 69 and 70 — the chat-broadcast tables, a kept chat's flag, saved chat lists.

``CREATE ... IF NOT EXISTS`` throughout: ``create_all`` runs BEFORE ``apply_migrations``,
so on a fresh database the schema comes from ``core.repositories.chat_broadcast._tables``
and this is a no-op. The two spellings must agree down to the CHECK constraints —
pinned by ``tests/core/test_migrations_chat_broadcast.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_chat_broadcast_tables(connection: Connection) -> None:
    _campaigns(connection)
    _accounts(connection)
    _targets(connection)
    _messages(connection)
    _events(connection)


def _campaigns(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS chat_broadcast_campaigns ("
        " campaign_id VARCHAR NOT NULL,"
        " name VARCHAR NOT NULL,"
        " status VARCHAR DEFAULT 'draft' NOT NULL,"
        " run_id VARCHAR,"
        " round INTEGER DEFAULT 0 NOT NULL,"
        " rest_until_unix INTEGER,"
        " started_unix INTEGER,"
        " resumed_unix INTEGER,"
        " finished_unix INTEGER,"
        " last_error VARCHAR,"
        " settings_json VARCHAR DEFAULT '{}' NOT NULL,"
        " created_at VARCHAR NOT NULL,"
        " updated_at VARCHAR NOT NULL,"
        " PRIMARY KEY (campaign_id),"
        " CHECK (status IN ('draft','running','stopping','stopped','done','failed','stalled')))",
    )


def _accounts(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS chat_broadcast_accounts ("
        " campaign_id VARCHAR NOT NULL,"
        " account_id VARCHAR NOT NULL,"
        " position INTEGER DEFAULT 0 NOT NULL,"
        " state VARCHAR DEFAULT 'active' NOT NULL,"
        " halted_reason VARCHAR,"
        " consecutive_errors INTEGER DEFAULT 0 NOT NULL,"
        " PRIMARY KEY (campaign_id, account_id),"
        " FOREIGN KEY(campaign_id) REFERENCES chat_broadcast_campaigns (campaign_id)"
        " ON DELETE CASCADE,"
        " FOREIGN KEY(account_id) REFERENCES accounts (account_id),"
        " CHECK (state IN ('active','halted')))",
    )


def _targets(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS chat_broadcast_targets ("
        " campaign_id VARCHAR NOT NULL,"
        " chat_key VARCHAR NOT NULL,"
        " position INTEGER NOT NULL,"
        " raw VARCHAR NOT NULL,"
        " kind VARCHAR NOT NULL,"
        " title VARCHAR,"
        " username VARCHAR,"
        " folder_slug VARCHAR,"
        " peer_id INTEGER,"
        " assigned_account_id VARCHAR,"
        " member_account_id VARCHAR,"
        " handed_from_account_id VARCHAR,"
        " state VARCHAR DEFAULT 'queued' NOT NULL,"
        " skip_reason VARCHAR,"
        " round INTEGER DEFAULT 1 NOT NULL,"
        " step_index INTEGER DEFAULT 0 NOT NULL,"
        " requested_unix INTEGER,"
        " joined_unix INTEGER,"
        " next_action_unix INTEGER,"
        " message_deleted INTEGER DEFAULT 0 NOT NULL,"
        " updated_unix INTEGER NOT NULL,"
        " PRIMARY KEY (campaign_id, chat_key),"
        " FOREIGN KEY(campaign_id) REFERENCES chat_broadcast_campaigns (campaign_id)"
        " ON DELETE CASCADE,"
        " CHECK (kind IN ('public','invite','folder','own')),"
        " CHECK (state IN ('queued','joining','captcha','pending_approval','waiting',"
        "'writing','reconnecting','waiting_account','round_done','done','skipped')))",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_cb_targets_account"
        " ON chat_broadcast_targets (campaign_id, assigned_account_id, state)",
    )


def _messages(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS chat_broadcast_messages ("
        " id INTEGER NOT NULL,"
        " campaign_id VARCHAR NOT NULL,"
        " run_id VARCHAR NOT NULL,"
        " chat_key VARCHAR NOT NULL,"
        " peer_id INTEGER,"
        " round INTEGER NOT NULL,"
        " step_index INTEGER NOT NULL,"
        " account_id VARCHAR NOT NULL,"
        " kind VARCHAR NOT NULL,"
        " text VARCHAR DEFAULT '' NOT NULL,"
        " rewritten INTEGER DEFAULT 0 NOT NULL,"
        " tg_message_id INTEGER,"
        " status VARCHAR NOT NULL,"
        " error_type VARCHAR,"
        " created_unix INTEGER NOT NULL,"
        " sent_unix INTEGER,"
        " PRIMARY KEY (id),"
        " FOREIGN KEY(campaign_id) REFERENCES chat_broadcast_campaigns (campaign_id)"
        " ON DELETE CASCADE,"
        " CHECK (kind IN ('text','photo','post')),"
        " CHECK (status IN ('pending','sent','failed','unconfirmed','deleted')))",
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_cb_messages_step"
        " ON chat_broadcast_messages (run_id, chat_key, round, step_index)",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_cb_messages_account"
        " ON chat_broadcast_messages (account_id, created_unix)",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_cb_messages_chat"
        " ON chat_broadcast_messages (chat_key, status)",
    )


def _events(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS chat_broadcast_events ("
        " id INTEGER NOT NULL,"
        " campaign_id VARCHAR NOT NULL,"
        " chat_key VARCHAR NOT NULL,"
        " round INTEGER NOT NULL,"
        " account_id VARCHAR,"
        " kind VARCHAR NOT NULL,"
        " detail VARCHAR,"
        " at_unix INTEGER NOT NULL,"
        " PRIMARY KEY (id),"
        " FOREIGN KEY(campaign_id) REFERENCES chat_broadcast_campaigns (campaign_id)"
        " ON DELETE CASCADE,"
        " CHECK (kind IN ('joined','already_member','requested','approved','captcha',"
        "'handed','skipped','reconnecting','deleted')))",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_cb_events_chat"
        " ON chat_broadcast_events (campaign_id, chat_key, at_unix)",
    )


def _add_chat_broadcast_ignore_deleted(connection: Connection) -> None:
    # #69: the operator may keep a chat in the broadcast although an admin deletes our
    # messages there; only a ban then stops it. 0 on existing rows = nobody chose that
    # yet. #68 always ran first, so the table is there; the column guard keeps it
    # idempotent (``create_all`` already built it on a fresh database).
    columns = {
        str(row["name"])
        for row in connection.exec_driver_sql("PRAGMA table_info(chat_broadcast_targets)")
        .mappings()
        .all()
    }
    if "ignore_deleted" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE chat_broadcast_targets "
            "ADD COLUMN ignore_deleted INTEGER DEFAULT 0 NOT NULL",
        )


def _add_chat_broadcast_collections(connection: Connection) -> None:
    # #70: the operator's saved chat lists, copied into a campaign when picked.
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS chat_broadcast_collections ("
        " collection_id VARCHAR NOT NULL,"
        " name VARCHAR NOT NULL,"
        " targets_json VARCHAR DEFAULT '[]' NOT NULL,"
        " created_at VARCHAR NOT NULL,"
        " updated_at VARCHAR NOT NULL,"
        " PRIMARY KEY (collection_id))",
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_cb_collections_name"
        " ON chat_broadcast_collections (name)",
    )
