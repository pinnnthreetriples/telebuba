"""SQLAlchemy tables for the masstagging domain.

Every ``CheckConstraint``, ``ForeignKey`` and index below MIRRORS
``core.migration_steps_masstagging``: ``create_all`` runs before ``apply_migrations``,
so on a fresh database THIS builds the schema and the migration no-ops on its
``IF NOT EXISTS``. ``tests/core/test_migrations_masstagging.py`` compares both.

Runtime instants are unix seconds (``*_unix``) because the engine compares them; the
campaign's ``updated_at`` stays an ISO stamp because it is the optimistic-lock token the
SPA echoes back, exactly like the chat broadcast's.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, String, Table, text

from core.db import _metadata

MODES = ("stories", "priming")
RUN_STATUSES = ("idle", "running", "stopping", "paused", "done", "failed")
TARGET_KINDS = ("username", "phone", "link", "contact")
TARGET_STATES = ("queued", "primed", "working", "tagged", "flood", "skipped", "failed")
EVENTS = ("dialog_created", "tagged", "flood", "skipped", "failed", "requeued", "now", "expanded")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({','.join(repr(value) for value in values)})"


_masstagging_campaigns = Table(
    "masstagging_campaigns",
    _metadata,
    Column("campaign_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("mode", String, nullable=False, server_default=text("'stories'")),
    Column("run_status", String, nullable=False, server_default=text("'idle'")),
    Column("run_id", String, nullable=True),
    Column("rest_until_unix", Integer, nullable=True),
    Column("started_unix", Integer, nullable=True),
    Column("session_unix", Integer, nullable=True),
    Column("finished_unix", Integer, nullable=True),
    Column("last_error", String, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    CheckConstraint(_in("mode", MODES)),
    CheckConstraint(_in("run_status", RUN_STATUSES)),
)

_masstagging_settings = Table(
    "masstagging_settings",
    _metadata,
    Column(
        "campaign_id",
        String,
        ForeignKey("masstagging_campaigns.campaign_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    # Everything the dialog edits, story media included, as one JSON document.
    Column("settings_json", String, nullable=False, server_default=text("'{}'")),
)

_masstagging_accounts = Table(
    "masstagging_accounts",
    _metadata,
    Column(
        "campaign_id",
        String,
        ForeignKey("masstagging_campaigns.campaign_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("account_id", String, ForeignKey("accounts.account_id"), primary_key=True),
    Column("position", Integer, nullable=False, server_default=text("0")),
)

_masstagging_targets = Table(
    "masstagging_targets",
    _metadata,
    Column(
        "campaign_id",
        String,
        ForeignKey("masstagging_campaigns.campaign_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("target_key", String, primary_key=True),
    Column("position", Integer, nullable=False),
    Column("raw", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("assigned_account_id", String, nullable=True),
    # A Telegram user id is per-account session state: ``resolved_by`` names the
    # account that can address ``resolved_user_id``.
    Column("resolved_user_id", Integer, nullable=True),
    Column("resolved_by", String, nullable=True),
    Column("username", String, nullable=True),
    Column("first_name", String, nullable=False, server_default=text("''")),
    Column("state", String, nullable=False, server_default=text("'queued'")),
    Column("reason", String, nullable=True),
    Column("account_id", String, nullable=True),
    Column("next_action_unix", Integer, nullable=True),
    Column("story_id", Integer, nullable=True),
    Column("updated_unix", Integer, nullable=False),
    CheckConstraint(_in("kind", TARGET_KINDS)),
    CheckConstraint(_in("state", TARGET_STATES)),
    Index("ix_mt_targets_state", "campaign_id", "state", "position"),
)

_masstagging_journal = Table(
    "masstagging_journal",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "campaign_id",
        String,
        ForeignKey("masstagging_campaigns.campaign_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("target_key", String, nullable=False),
    Column("event", String, nullable=False),
    Column("at_unix", Integer, nullable=False),
    Column("account_id", String, nullable=True),
    # A stable code or a number — never prose, the SPA words it.
    Column("detail", String, nullable=True),
    CheckConstraint(_in("event", EVENTS)),
    Index("ix_mt_journal_target", "campaign_id", "target_key", "at_unix"),
)

# Everyone any campaign ever tagged: «Пропускать уже отмеченных» reads it across all of
# them, so it outlives the campaign that wrote the row.
_masstagging_tagged_registry = Table(
    "masstagging_tagged_registry",
    _metadata,
    Column("user_id", Integer, primary_key=True),
    Column("campaign_id", String, nullable=False),
    Column("at_unix", Integer, nullable=False),
)

# Per-account counters of one masstagging day; Telegram's limits are per account,
# whatever campaign spends them.
_masstagging_account_day = Table(
    "masstagging_account_day",
    _metadata,
    Column("account_id", String, primary_key=True),
    Column("day", String, primary_key=True),
    Column("stories", Integer, nullable=False, server_default=text("0")),
    Column("dialogs", Integer, nullable=False, server_default=text("0")),
    Column("tags", Integer, nullable=False, server_default=text("0")),
    Column("resolves", Integer, nullable=False, server_default=text("0")),
    Column("floods_in_row", Integer, nullable=False, server_default=text("0")),
    Column("paused_until_unix", Integer, nullable=True),
)
