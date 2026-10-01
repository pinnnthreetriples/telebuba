"""SQLAlchemy tables for timed profile-photo / story publishing.

Every ``CheckConstraint``, ``ForeignKey`` and index MIRRORS
``core.migration_steps_scheduled_posts``: ``create_all`` builds a fresh database
from THIS module while an existing one gets the migration, and
``tests/core/test_migrations_scheduled_posts.py`` compares the two.

The account foreign key has no ``ON DELETE CASCADE`` (the house rule — see
``core.repositories._accounts_delete``); the media rows cascade from their post,
which belongs to this domain alone.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, String, Table, text

from core.db import _metadata

_scheduled_profile_posts = Table(
    "scheduled_profile_posts",
    _metadata,
    Column("post_id", String, primary_key=True),
    Column("account_id", String, ForeignKey("accounts.account_id"), nullable=False),
    Column("kind", String, nullable=False),
    Column("state", String, nullable=False, server_default=text("'pending'")),
    # ``dispatching`` is written BEFORE the Telegram call: a restart that finds it
    # cannot know whether the post went out, so recovery never repeats it.
    Column("stage", String, nullable=False, server_default=text("'queued'")),
    # The operator's time (only a reschedule moves it) and the runtime's next try.
    Column("scheduled_for_unix", Integer, nullable=False),
    Column("next_attempt_unix", Integer, nullable=False),
    Column("dispatch_started_unix", Integer, nullable=True),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("filename", String, nullable=True),
    # ``ScheduledStoryOptions`` as JSON for a story, NULL for a photo.
    Column("story_json", String, nullable=True),
    Column("batch_id", String, nullable=True),
    Column("client_key", String, nullable=True),
    # A stable code, never ``str(exc)``: this row is served back by the API.
    Column("error_code", String, nullable=True),
    Column("story_id", Integer, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_unix", Integer, nullable=False),
    Column("finished_unix", Integer, nullable=True),
    CheckConstraint("kind IN ('photo','story')"),
    CheckConstraint(
        "state IN ('pending','processing','done','failed','cancelled','missed','ambiguous')",
    ),
    CheckConstraint("stage IN ('queued','dispatching')"),
    Index("ix_sched_posts_due", "state", "next_attempt_unix"),
    Index("ix_sched_posts_account", "account_id", "scheduled_for_unix"),
    # SQLite treats NULLs as distinct, so only keyed (bulk) rows are deduplicated.
    Index("ux_sched_posts_client", "batch_id", "account_id", "client_key", unique=True),
)

_scheduled_post_media = Table(
    "scheduled_post_media",
    _metadata,
    Column(
        "post_id",
        String,
        ForeignKey("scheduled_profile_posts.post_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("position", Integer, primary_key=True),
    Column("media_name", String, nullable=False),
    Index("ix_sched_media_name", "media_name"),
)
