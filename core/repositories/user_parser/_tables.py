"""SQLAlchemy tables for the user parser.

Every ``CheckConstraint``, ``ForeignKey`` and index below MIRRORS
``core.migration_steps_user_parser``: ``create_all`` runs before ``apply_migrations``, so
on a fresh database THIS builds the schema and the migration no-ops on its
``IF NOT EXISTS``. ``tests/core/test_migrations_user_parser.py`` compares both.

A run is also a base («Базы»): nothing deletes one but the operator, and its people go
with it (``ON DELETE CASCADE``). ``name_key`` on presets is the casefolded name, for the
reason ``account_folders`` gives: SQLite's ``NOCASE`` folds ASCII only.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, String, Table, text

from core.db import _metadata

RUN_STATUSES = ("running", "done", "stopped", "failed", "interrupted")
RUN_MODES = ("members", "messages", "comments")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({','.join(repr(value) for value in values)})"


_user_parser_runs = Table(
    "user_parser_runs",
    _metadata,
    Column("run_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("finished_at", String),
    Column("status", String, nullable=False),
    Column("mode", String, nullable=False),
    Column("request_json", String, nullable=False),
    Column("account_ids_json", String, nullable=False),
    Column("sources_total", Integer, nullable=False, server_default=text("0")),
    Column("sources_done", Integer, nullable=False, server_default=text("0")),
    Column("collected_raw", Integer, nullable=False, server_default=text("0")),
    Column("kept", Integer, nullable=False, server_default=text("0")),
    Column("stop_reason", String),
    Column("sources_json", String, nullable=False, server_default=text("'[]'")),
    Column("filtered_json", String, nullable=False, server_default=text("'{}'")),
    CheckConstraint(_in("status", RUN_STATUSES), name="ck_user_parser_runs_status"),
    CheckConstraint(_in("mode", RUN_MODES), name="ck_user_parser_runs_mode"),
    Index("ix_user_parser_runs_created_at", "created_at"),
)

_user_parser_users = Table(
    "user_parser_users",
    _metadata,
    Column(
        "run_id",
        String,
        ForeignKey("user_parser_runs.run_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("user_id", Integer, primary_key=True),
    Column("username", String),
    Column("first_name", String, nullable=False, server_default=text("''")),
    Column("last_name", String, nullable=False, server_default=text("''")),
    Column("is_bot", Integer, nullable=False, server_default=text("0")),
    Column("is_deleted", Integer, nullable=False, server_default=text("0")),
    Column("is_scam", Integer, nullable=False, server_default=text("0")),
    Column("is_fake", Integer, nullable=False, server_default=text("0")),
    Column("is_premium", Integer, nullable=False, server_default=text("0")),
    Column("has_photo", Integer, nullable=False, server_default=text("0")),
    Column("has_stories", Integer, nullable=False, server_default=text("0")),
    Column("last_seen", String, nullable=False),
    Column("message_count", Integer, nullable=False, server_default=text("0")),
    Column("first_at", String),
    Column("last_at", String),
    Column("sources_json", String, nullable=False, server_default=text("'[]'")),
    # Casefolded "name username id" for the bases' search: SQLite's ``lower()`` and
    # ``LIKE`` fold ASCII only, so a Cyrillic name would not be found in another case.
    Column("search_key", String, nullable=False, server_default=text("''")),
    Index("ix_user_parser_users_user_id", "user_id"),
)

_user_parser_presets = Table(
    "user_parser_presets",
    _metadata,
    Column("preset_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("name_key", String, nullable=False),
    Column("settings_json", String, nullable=False),
    Column("created_at", String, nullable=False),
    Index("ux_user_parser_presets_name_key", "name_key", unique=True),
)
