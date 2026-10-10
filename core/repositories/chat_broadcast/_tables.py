"""SQLAlchemy tables for the chat-broadcast domain.

Every ``CheckConstraint``, ``ForeignKey`` and index below MIRRORS
``core.migration_steps_chat_broadcast``: ``create_all`` runs before ``apply_migrations``,
so on a fresh database THIS builds the schema and the migration no-ops on its
``IF NOT EXISTS``. ``tests/core/test_migrations_chat_broadcast.py`` compares both.

Runtime instants are unix seconds (``*_unix``) because the engine compares them; the
campaign's ``updated_at`` stays an ISO stamp because it is the optimistic-lock token
the SPA echoes back, exactly like neuroshilling's.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, String, Table, text

from core.db import _metadata

CAMPAIGN_STATUSES = ("draft", "running", "stopping", "stopped", "done", "failed", "stalled")
TARGET_STATES = (
    "queued",
    "joining",
    "captcha",
    "pending_approval",
    "waiting",
    "writing",
    "reconnecting",
    "waiting_account",
    "round_done",
    "done",
    "skipped",
)
MESSAGE_STATUSES = ("pending", "sent", "failed", "unconfirmed", "deleted")
EVENT_KINDS = (
    "joined",
    "already_member",
    "requested",
    "approved",
    "captcha",
    "handed",
    "skipped",
    "reconnecting",
    "deleted",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({','.join(repr(value) for value in values)})"


_chat_broadcast_campaigns = Table(
    "chat_broadcast_campaigns",
    _metadata,
    Column("campaign_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("status", String, nullable=False, server_default=text("'draft'")),
    Column("run_id", String, nullable=True),
    Column("round", Integer, nullable=False, server_default=text("0")),
    Column("rest_until_unix", Integer, nullable=True),
    Column("started_unix", Integer, nullable=True),
    Column("resumed_unix", Integer, nullable=True),
    Column("finished_unix", Integer, nullable=True),
    Column("last_error", String, nullable=True),
    Column("settings_json", String, nullable=False, server_default=text("'{}'")),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    CheckConstraint(_in("status", CAMPAIGN_STATUSES)),
)

_chat_broadcast_accounts = Table(
    "chat_broadcast_accounts",
    _metadata,
    Column(
        "campaign_id",
        String,
        ForeignKey("chat_broadcast_campaigns.campaign_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("account_id", String, ForeignKey("accounts.account_id"), primary_key=True),
    Column("position", Integer, nullable=False, server_default=text("0")),
    Column("state", String, nullable=False, server_default=text("'active'")),
    Column("halted_reason", String, nullable=True),
    Column("consecutive_errors", Integer, nullable=False, server_default=text("0")),
    CheckConstraint("state IN ('active','halted')"),
)

_chat_broadcast_targets = Table(
    "chat_broadcast_targets",
    _metadata,
    Column(
        "campaign_id",
        String,
        ForeignKey("chat_broadcast_campaigns.campaign_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("chat_key", String, primary_key=True),
    Column("position", Integer, nullable=False),
    Column("raw", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("title", String, nullable=True),
    Column("username", String, nullable=True),
    Column("folder_slug", String, nullable=True),
    # Raw positive id, meaningful to ``member_account_id`` only (see
    # ``schemas.telegram_actions_chat``); a folder or own group's id is known up front.
    Column("peer_id", Integer, nullable=True),
    Column("assigned_account_id", String, nullable=True),
    Column("member_account_id", String, nullable=True),
    Column("handed_from_account_id", String, nullable=True),
    Column("state", String, nullable=False, server_default=text("'queued'")),
    Column("skip_reason", String, nullable=True),
    Column("round", Integer, nullable=False, server_default=text("1")),
    Column("step_index", Integer, nullable=False, server_default=text("0")),
    Column("requested_unix", Integer, nullable=True),
    Column("joined_unix", Integer, nullable=True),
    Column("next_action_unix", Integer, nullable=True),
    Column("message_deleted", Integer, nullable=False, server_default=text("0")),
    # The operator chose to keep writing here despite deleted messages; only a ban stops
    # it. Added by migration 69, so it follows ``updated_unix`` in a migrated table.
    Column("ignore_deleted", Integer, nullable=False, server_default=text("0")),
    Column("updated_unix", Integer, nullable=False),
    CheckConstraint("kind IN ('public','invite','folder','own')"),
    CheckConstraint(_in("state", TARGET_STATES)),
    Index("ix_cb_targets_account", "campaign_id", "assigned_account_id", "state"),
)

_chat_broadcast_messages = Table(
    "chat_broadcast_messages",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "campaign_id",
        String,
        ForeignKey("chat_broadcast_campaigns.campaign_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("run_id", String, nullable=False),
    Column("chat_key", String, nullable=False),
    Column("peer_id", Integer, nullable=True),
    Column("round", Integer, nullable=False),
    Column("step_index", Integer, nullable=False),
    Column("account_id", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("text", String, nullable=False, server_default=text("''")),
    Column("rewritten", Integer, nullable=False, server_default=text("0")),
    Column("tg_message_id", Integer, nullable=True),
    Column("status", String, nullable=False),
    Column("error_type", String, nullable=True),
    Column("created_unix", Integer, nullable=False),
    Column("sent_unix", Integer, nullable=True),
    CheckConstraint("kind IN ('text','photo','post')"),
    CheckConstraint(_in("status", MESSAGE_STATUSES)),
    # Written ``pending`` BEFORE the send: an occupied key is a step already played,
    # which is what keeps a resumed run from writing into a chat twice.
    Index("ux_cb_messages_step", "run_id", "chat_key", "round", "step_index", unique=True),
    Index("ix_cb_messages_account", "account_id", "created_unix"),
    Index("ix_cb_messages_chat", "chat_key", "status"),
)

_chat_broadcast_events = Table(
    "chat_broadcast_events",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "campaign_id",
        String,
        ForeignKey("chat_broadcast_campaigns.campaign_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("chat_key", String, nullable=False),
    Column("round", Integer, nullable=False),
    Column("account_id", String, nullable=True),
    Column("kind", String, nullable=False),
    # A stable code or a number (a skip reason, the account a chat came from, the unix
    # time the first message is due) — never prose, the SPA words it.
    Column("detail", String, nullable=True),
    Column("at_unix", Integer, nullable=False),
    CheckConstraint(_in("kind", EVENT_KINDS)),
    Index("ix_cb_events_chat", "campaign_id", "chat_key", "at_unix"),
)

# The operator's saved chat lists («категории»). A list is COPIED into a campaign's
# settings when picked, never linked: editing it later changes no campaign.
_chat_broadcast_collections = Table(
    "chat_broadcast_collections",
    _metadata,
    Column("collection_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("targets_json", String, nullable=False, server_default=text("'[]'")),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Index("ux_cb_collections_name", "name", unique=True),
)
