"""Migration 67 — the scheduled profile-post tables.

``CREATE ... IF NOT EXISTS`` throughout: ``create_all`` runs BEFORE
``apply_migrations``, so on a fresh database the schema comes from
``core.repositories.scheduled_posts._tables`` and this is a no-op. The two
spellings must agree down to the CHECK constraints — pinned by
``tests/core/test_migrations_scheduled_posts.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_scheduled_posts_tables(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS scheduled_profile_posts ("
        " post_id VARCHAR NOT NULL PRIMARY KEY,"
        " account_id VARCHAR NOT NULL REFERENCES accounts (account_id),"
        " kind VARCHAR NOT NULL CHECK (kind IN ('photo','story')),"
        " state VARCHAR NOT NULL DEFAULT 'pending'"
        " CHECK (state IN ('pending','processing','done','failed','cancelled',"
        "'missed','ambiguous')),"
        " stage VARCHAR NOT NULL DEFAULT 'queued'"
        " CHECK (stage IN ('queued','dispatching')),"
        " scheduled_for_unix INTEGER NOT NULL,"
        " next_attempt_unix INTEGER NOT NULL,"
        " dispatch_started_unix INTEGER,"
        " attempts INTEGER NOT NULL DEFAULT 0,"
        " filename VARCHAR,"
        " story_json VARCHAR,"
        " batch_id VARCHAR,"
        " client_key VARCHAR,"
        " error_code VARCHAR,"
        " story_id INTEGER,"
        " created_at VARCHAR NOT NULL,"
        " updated_unix INTEGER NOT NULL,"
        " finished_unix INTEGER)",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_sched_posts_due"
        " ON scheduled_profile_posts (state, next_attempt_unix)",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_sched_posts_account"
        " ON scheduled_profile_posts (account_id, scheduled_for_unix)",
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_sched_posts_client"
        " ON scheduled_profile_posts (batch_id, account_id, client_key)",
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS scheduled_post_media ("
        " post_id VARCHAR NOT NULL"
        " REFERENCES scheduled_profile_posts (post_id) ON DELETE CASCADE,"
        " position INTEGER NOT NULL,"
        " media_name VARCHAR NOT NULL,"
        " PRIMARY KEY (post_id, position))",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_sched_media_name ON scheduled_post_media (media_name)",
    )


def _add_scheduled_posts_downgraded(connection: Connection) -> None:
    # Migration 110: which Premium features a free account lost when the story went out.
    # NULL for every existing row (and every photo): nothing was dropped.
    if not _sqlite_table_exists(connection, "scheduled_profile_posts"):
        return
    # Outside ``_ALLOWED_TABLES``, so the column probe is inlined (a fixed table name).
    rows = connection.exec_driver_sql("PRAGMA table_info(scheduled_profile_posts)").mappings().all()
    if "downgraded" not in {str(row["name"]) for row in rows}:
        connection.exec_driver_sql(
            "ALTER TABLE scheduled_profile_posts ADD COLUMN downgraded VARCHAR",
        )
