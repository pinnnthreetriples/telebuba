"""Migration 72 — the user parser: runs (each one a saved base), their people, presets.

``CREATE ... IF NOT EXISTS`` throughout: ``create_all`` runs BEFORE ``apply_migrations``,
so on a fresh database the schema comes from ``core.repositories.user_parser._tables``
and this is a no-op. The two spellings must agree down to the CHECK constraints —
pinned by ``tests/core/test_migrations_user_parser.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_user_parser_tables(connection: Connection) -> None:
    _runs(connection)
    _users(connection)
    _presets(connection)


def _runs(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS user_parser_runs (run_id VARCHAR NOT NULL, "
        "name VARCHAR NOT NULL, created_at VARCHAR NOT NULL, "
        "finished_at VARCHAR, status VARCHAR NOT NULL, mode VARCHAR NOT NULL, "
        "request_json VARCHAR NOT NULL, account_ids_json VARCHAR NOT NULL, "
        "sources_total INTEGER DEFAULT 0 NOT NULL, "
        "sources_done INTEGER DEFAULT 0 NOT NULL, "
        "collected_raw INTEGER DEFAULT 0 NOT NULL, "
        "kept INTEGER DEFAULT 0 NOT NULL, stop_reason VARCHAR, "
        "sources_json VARCHAR DEFAULT '[]' NOT NULL, "
        "filtered_json VARCHAR DEFAULT '{}' NOT NULL, PRIMARY KEY (run_id), "
        "CONSTRAINT ck_user_parser_runs_status CHECK "
        "(status IN ('running','done','stopped','failed','interrupted')), "
        "CONSTRAINT ck_user_parser_runs_mode CHECK (mode IN ('members','messages','comments')) )",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_user_parser_runs_created_at"
        " ON user_parser_runs (created_at)",
    )


def _users(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS user_parser_users (run_id VARCHAR NOT NULL, "
        "user_id INTEGER NOT NULL, username VARCHAR, "
        "first_name VARCHAR DEFAULT '' NOT NULL, "
        "last_name VARCHAR DEFAULT '' NOT NULL, "
        "is_bot INTEGER DEFAULT 0 NOT NULL, "
        "is_deleted INTEGER DEFAULT 0 NOT NULL, "
        "is_scam INTEGER DEFAULT 0 NOT NULL, "
        "is_fake INTEGER DEFAULT 0 NOT NULL, "
        "is_premium INTEGER DEFAULT 0 NOT NULL, "
        "has_photo INTEGER DEFAULT 0 NOT NULL, "
        "has_stories INTEGER DEFAULT 0 NOT NULL, last_seen VARCHAR NOT NULL, "
        "message_count INTEGER DEFAULT 0 NOT NULL, first_at VARCHAR, "
        "last_at VARCHAR, sources_json VARCHAR DEFAULT '[]' NOT NULL, "
        "search_key VARCHAR DEFAULT '' NOT NULL, "
        "PRIMARY KEY (run_id, user_id), "
        "FOREIGN KEY(run_id) REFERENCES user_parser_runs (run_id) ON DELETE CASCADE )",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_user_parser_users_user_id ON user_parser_users (user_id)",
    )


def _presets(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS user_parser_presets (preset_id VARCHAR NOT NULL, "
        "name VARCHAR NOT NULL, name_key VARCHAR NOT NULL, "
        "settings_json VARCHAR NOT NULL, created_at VARCHAR NOT NULL, "
        "PRIMARY KEY (preset_id) )",
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_user_parser_presets_name_key"
        " ON user_parser_presets (name_key)",
    )
