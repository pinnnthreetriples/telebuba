"""SQLAlchemy tables for the neurocomment domain (issue #114).

Kept in their own module so ``core.db`` stays within the file-size budget.
Importing this module registers the tables in ``core.db._metadata``; the
repository package pulls it in, and ``core.db`` imports the package before
``_get_engine`` runs ``create_all``. The partial unique index enforcing
"one active campaign per channel" is created in migration #11.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
)

from core.db import _metadata

_neurocomment_campaigns = Table(
    "neurocomment_campaigns",
    _metadata,
    Column("campaign_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("prompt", String, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    # Per-campaign solver override (#14): NULL defers to the global flag (#148).
    Column("solver_enabled", Boolean, nullable=True),
)
_neurocomment_campaign_channels = Table(
    "neurocomment_campaign_channels",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "campaign_id",
        String,
        ForeignKey("neurocomment_campaigns.campaign_id"),
        nullable=False,
    ),
    Column("channel", String, nullable=False),
    Column("active", Integer, nullable=False),
    Column("created_at", String, nullable=False),
)
_neurocomment_campaign_accounts = Table(
    "neurocomment_campaign_accounts",
    _metadata,
    Column(
        "campaign_id",
        String,
        ForeignKey("neurocomment_campaigns.campaign_id"),
        primary_key=True,
    ),
    Column("account_id", String, ForeignKey("accounts.account_id"), primary_key=True),
    Column("created_at", String, nullable=False),
    # Legacy single-channel pin (migration #25): superseded by the subset table
    # below (migration #29) and no longer read or written — kept only because
    # migrations are add-only. NULL = all campaign channels.
    Column("channel", String, nullable=True),
)
# Per-account channel SUBSET within a campaign (migration #29): one row per channel
# the account is pinned to. NO rows for a (campaign, account) pair = the account
# serves ALL of the campaign's channels (the default). Supersedes the scalar
# ``channel`` column above.
_neurocomment_campaign_account_channels = Table(
    "neurocomment_campaign_account_channels",
    _metadata,
    Column(
        "campaign_id",
        String,
        ForeignKey("neurocomment_campaigns.campaign_id"),
        primary_key=True,
    ),
    Column("account_id", String, ForeignKey("accounts.account_id"), primary_key=True),
    Column("channel", String, primary_key=True),
    Column("created_at", String, nullable=False),
)
_neurocomment_linked_groups = Table(
    "neurocomment_linked_groups",
    _metadata,
    Column("channel", String, primary_key=True),
    Column("linked_chat_id", BigInteger, nullable=True),
    Column("comments_enabled", Integer, nullable=False),
    Column("checked_at", String, nullable=False),
)
_neurocomment_readiness = Table(
    "neurocomment_readiness",
    _metadata,
    Column("account_id", String, ForeignKey("accounts.account_id"), primary_key=True),
    Column("channel", String, primary_key=True),
    Column("joined", Integer, nullable=False),
    Column("captcha_passed", Integer, nullable=False),
    Column("ready", Integer, nullable=False),
    Column("checked_at", String, nullable=False),
    # Operator "Skip channel for this account" (#148); migration #15 backfills 0.
    Column("human_skipped", Integer, nullable=False, server_default="0"),
    # Auto-detected hard ban: the account got UserBannedInChannelError posting here.
    # Sticky (survives re-onboarding); migration #30 backfills 0. Cleared by a
    # successful "Проверить каналы" probe (can_send) or an operator retry.
    Column("banned", Integer, nullable=False, server_default="0"),
)
_neurocomment_comments = Table(
    "neurocomment_comments",
    _metadata,
    Column("channel", String, primary_key=True),
    Column("post_id", Integer, primary_key=True),
    Column(
        "campaign_id",
        String,
        ForeignKey("neurocomment_campaigns.campaign_id"),
        nullable=False,
    ),
    Column("account_id", String, ForeignKey("accounts.account_id"), nullable=False),
    Column("status", String, nullable=False),
    Column("comment_text", String, nullable=True),
    Column("comment_msg_id", Integer, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    # Set (migration #27) when a posted comment is later found deleted from the
    # channel — NULL = still live. Its status stays 'posted' (it *was* delivered).
    Column("deleted_at", String, nullable=True),
)
# Challenge audit-and-cache table (migration #14): one row per guardian-bot
# challenge encountered at onboarding. Doubles as the global solved-decision
# cache (a ``WHERE outcome='solved'`` projection). Indexes live in migration #14.
_neurocomment_challenges = Table(
    "neurocomment_challenges",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("challenge_hash", String, nullable=False),
    Column("account_id", String, nullable=False),
    Column("channel", String, nullable=False),
    Column("raw_text", String, nullable=False),
    Column("button_labels_json", String, nullable=False),
    Column("decision_json", String, nullable=True),
    Column("outcome", String, nullable=False),
    Column("decided_at", String, nullable=False),
    Column("outcome_at", String, nullable=True),
)
# Single-row table holding the active listener account id so the engine can
# re-point the listener at boot. ``id`` is pinned to 1 (migration #12).
# ``listener_running`` (migration #24) splits "which account is the listener"
# from "is the runtime actively subscribed": a paused runtime keeps its remembered
# ``listener_account_id`` while ``listener_running`` is 0, so reload/reboot no
# longer confuses pause with "снять слушателя" (remove).
_neurocomment_runtime = Table(
    "neurocomment_runtime",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("listener_account_id", String, nullable=True),
    Column("listener_running", Boolean, nullable=False, server_default="0"),
    Column("updated_at", String, nullable=False),
    CheckConstraint("id = 1", name="ck_neurocomment_runtime_single_row"),
)
# Single-row operator-editable neurocomment limits (migration #19). Empty until
# the operator saves; reads fall back to ``settings.neurocomment`` config.
_neurocomment_settings = Table(
    "neurocomment_settings",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("max_comments_per_hour", Integer, nullable=False),
    Column("max_comments_per_channel_per_day", Integer, nullable=False),
    Column("reply_delay_min_seconds", Float, nullable=False),
    Column("reply_delay_max_seconds", Float, nullable=False),
    Column("min_trust_score", Integer, nullable=False),
    Column("updated_at", String, nullable=False),
    CheckConstraint("id = 1", name="ck_neurocomment_settings_single_row"),
)
