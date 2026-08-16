"""SQLAlchemy tables for the neuroshilling domain.

Kept in their own module so ``core.db`` stays within the file-size budget.
Importing this module registers the tables in ``core.db._metadata``; the
repository package pulls it in, and the services import that package directly
(``core.db`` deliberately gains no re-export block for this domain — it is
already close to its own budget and every neuroshilling caller is new code).

Every ``CheckConstraint`` and every ``ForeignKey`` below MIRRORS
``core.migration_steps_neuroshilling`` on purpose. ``core.db._build_engine``
runs ``create_all`` BEFORE ``apply_migrations``, so on a fresh database THIS is
what actually builds the schema and the migration silently no-ops on its
``IF NOT EXISTS``. A constraint declared only in the migration would therefore
exist on upgraded databases and be missing on new ones —
``tests/core/test_migrations_neuroshilling.py`` compares both schemas so the two
spellings cannot drift.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    text,
)

from core.db import _metadata

_neuroshilling_campaigns = Table(
    "neuroshilling_campaigns",
    _metadata,
    Column("campaign_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("mode", String, nullable=False, server_default=text("'campaign'")),
    Column("topic", String, nullable=False, server_default=text("''")),
    # The operator's target blob exactly as typed; the normalised list is derived.
    Column("targets_raw", String, nullable=False, server_default=text("''")),
    Column("unique_messages", Integer, nullable=False, server_default=text("1")),
    Column("use_chat_context", Integer, nullable=False, server_default=text("0")),
    Column("media_message_link", String, nullable=True),
    Column("media_step_position", Integer, nullable=True),
    Column("scenario_status", String, nullable=False, server_default=text("'draft'")),
    Column("run_mode", String, nullable=False, server_default=text("'sequential'")),
    # SECONDS between targets — minimum and maximum, never minutes.
    Column("pause_min_seconds", Integer, nullable=False, server_default=text("10")),
    Column("pause_max_seconds", Integer, nullable=False, server_default=text("20")),
    # Aligned with the project's own neurocomment ceilings (10/hour, 3/chat/day):
    # an unsolicited post into a group we just joined is strictly more reportable
    # than a comment under a post that formally invited one.
    Column("messages_per_hour", Integer, nullable=False, server_default=text("10")),
    Column("messages_per_chat_per_day", Integer, nullable=False, server_default=text("3")),
    # NULL = no ceiling.
    Column("total_per_account", Integer, nullable=True),
    Column("reserve_enabled", Integer, nullable=False, server_default=text("0")),
    Column("autoresponder", String, nullable=False, server_default=text("'off'")),
    Column("reply_to_humans", Integer, nullable=False, server_default=text("0")),
    Column("reply_activity", String, nullable=False, server_default=text("'medium'")),
    Column("listen_minutes", Integer, nullable=False, server_default=text("60")),
    Column("status", String, nullable=False, server_default=text("'idle'")),
    Column("run_id", String, nullable=True),
    # Class NAME only, never ``str(exc)``: this row is served back by the API.
    Column("last_error", String, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    CheckConstraint("mode IN ('campaign','revive')"),
    CheckConstraint("scenario_status IN ('draft','approved')"),
    CheckConstraint("run_mode IN ('sequential','parallel')"),
    CheckConstraint("autoresponder IN ('off','neurodialog')"),
    CheckConstraint("reply_activity IN ('calm','medium','active')"),
    CheckConstraint("status IN ('idle','running','stopping','done','failed')"),
)

_neuroshilling_roles = Table(
    "neuroshilling_roles",
    _metadata,
    Column("role_id", String, primary_key=True),
    Column(
        "campaign_id",
        String,
        ForeignKey("neuroshilling_campaigns.campaign_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("name", String, nullable=False),
    # This IS the persona prompt, which is why it is not a label-sized column.
    Column("description", String, nullable=False, server_default=text("''")),
    Column("created_at", String, nullable=False),
    Index("ix_ns_roles_campaign", "campaign_id", "created_at"),
)

_neuroshilling_accounts = Table(
    "neuroshilling_accounts",
    _metadata,
    Column(
        "campaign_id",
        String,
        ForeignKey("neuroshilling_campaigns.campaign_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("account_id", String, ForeignKey("accounts.account_id"), primary_key=True),
    Column(
        "role_id",
        String,
        ForeignKey("neuroshilling_roles.role_id", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("is_reserve", Integer, nullable=False, server_default=text("0")),
    Column("state", String, nullable=False, server_default=text("'active'")),
    Column("replaced_by_account_id", String, nullable=True),
    Column("created_at", String, nullable=False),
    # Only two of the three are ever written: ``ban_campaign_account`` sets ``banned``
    # and nothing else moves the column. A promoted reserve is not a third state — it
    # keeps ``active`` and loses its ``is_reserve`` flag — so ``replaced`` is dead
    # vocabulary, and it stays anyway: migration 55 is stamped on deployed databases
    # with this list in it, and narrowing the spelling here would leave the two schemas
    # disagreeing on every database that has already run it.
    CheckConstraint("state IN ('active','banned','replaced')"),
    Index("ix_ns_accounts_role", "campaign_id", "role_id"),
)

_neuroshilling_steps = Table(
    "neuroshilling_steps",
    _metadata,
    Column("step_id", String, primary_key=True),
    Column(
        "campaign_id",
        String,
        ForeignKey("neuroshilling_campaigns.campaign_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("position", Integer, nullable=False),
    Column("kind", String, nullable=False),
    Column(
        "role_id",
        String,
        ForeignKey("neuroshilling_roles.role_id", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("text", String, nullable=False, server_default=text("''")),
    # Both point at POSITIONS, not step ids, so a regenerated scenario keeps shape.
    Column("reply_to_position", Integer, nullable=True),
    Column("target_position", Integer, nullable=True),
    Column("emoji", String, nullable=True),
    Column("delay_min_seconds", Integer, nullable=False, server_default=text("60")),
    Column("delay_max_seconds", Integer, nullable=False, server_default=text("180")),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    CheckConstraint("position >= 1"),
    CheckConstraint("kind IN ('message','reaction')"),
    CheckConstraint("delay_min_seconds BETWEEN 0 AND 3600"),
    CheckConstraint("delay_max_seconds BETWEEN 0 AND 3600"),
    Index("ux_ns_steps_position", "campaign_id", "position", unique=True),
)

_neuroshilling_presence = Table(
    "neuroshilling_presence",
    _metadata,
    Column(
        "campaign_id",
        String,
        ForeignKey("neuroshilling_campaigns.campaign_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("account_id", String, primary_key=True),
    Column("target", String, primary_key=True),
    Column("state", String, nullable=False, server_default=text("'pending'")),
    # Class NAME, not message text — this travels to the API like everything else.
    Column("last_error_type", String, nullable=True),
    Column("joined_at", String, nullable=True),
    Column("updated_at", String, nullable=False),
    CheckConstraint(
        "state IN ('pending','joined','pending_approval','refused','flooded','retired')",
    ),
    Index("ix_ns_presence_target", "campaign_id", "target", "state"),
)

_neuroshilling_messages = Table(
    "neuroshilling_messages",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "campaign_id",
        String,
        ForeignKey("neuroshilling_campaigns.campaign_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("run_id", String, nullable=False),
    Column("target", String, nullable=False),
    Column("step_id", String, ForeignKey("neuroshilling_steps.step_id"), nullable=False),
    Column("account_id", String, nullable=False),
    Column("text", String, nullable=False, server_default=text("''")),
    Column("message_id", Integer, nullable=True),
    Column("status", String, nullable=False),
    Column("error_type", String, nullable=True),
    Column("sent_at", String, nullable=True),
    Column("created_at", String, nullable=False),
    CheckConstraint("status IN ('pending','sent','failed','skipped')"),
    Index("ux_ns_messages_step", "run_id", "target", "step_id", unique=True),
    # Keyed on created_at, NOT sent_at: the quota predicate counts
    # ``status IN ('pending','sent')`` and a pending row has no sent_at yet.
    Index("ix_ns_messages_account_created", "account_id", "created_at"),
    Index("ix_ns_messages_chat_day", "account_id", "target", "created_at"),
)


# Every message the poller has seen in a target chat — the only table in the
# domain whose ``text`` column is written by strangers. See
# ``core.migration_steps_neuroshilling_chat`` for what each column means; the two
# spellings mirror each other for the reason in this module's docstring.
_neuroshilling_chat_log = Table(
    "neuroshilling_chat_log",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "campaign_id",
        String,
        ForeignKey("neuroshilling_campaigns.campaign_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("target", String, nullable=False),
    Column("message_id", Integer, nullable=False),
    # SQLite's INTEGER is already 64-bit, so a BigInteger spelling would only be a
    # second name for the same affinity and the two schemas have to match exactly.
    Column("sender_id", Integer, nullable=True),
    Column("text", String, nullable=False, server_default=text("''")),
    Column("is_ours", Integer, nullable=False, server_default=text("0")),
    # A DECISION, not an outcome: set when a message is picked for an answer,
    # whatever becomes of that answer, so nothing is ever answered twice.
    Column("replied", Integer, nullable=False, server_default=text("0")),
    Column("reply_account_id", String, nullable=True),
    Column("replied_at", String, nullable=True),
    Column("seen_at", String, nullable=False),
    # Idempotent re-polling AND the poll cursor: ``MAX(message_id)`` for a
    # (campaign, target) is a prefix scan of this index, so there is no cursor table.
    Index("ux_ns_chat_log_msg", "campaign_id", "target", "message_id", unique=True),
    Index("ix_ns_chat_log_reply", "reply_account_id", "replied_at"),
)
