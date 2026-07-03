"""SQLAlchemy schema — the shared ``MetaData`` and every core table definition.

Split out of :mod:`core.db` for the file-size budget. ``core.db`` imports the
metadata and table objects back so ``from core.db import _accounts`` etc. keep
working, and so the repositories that ``from core.db import _accounts`` are
unaffected. This module owns no engine lifecycle or helpers — pure table DDL.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
)

_metadata = MetaData()
_device_fingerprints = Table(
    "device_fingerprints",
    _metadata,
    Column("account_id", String, primary_key=True),
    Column("platform", String, nullable=False),
    Column("device_model", String, nullable=False),
    Column("system_version", String, nullable=False),
    Column("app_version", String, nullable=False),
    Column("lang_code", String, nullable=False),
    Column("system_lang_code", String, nullable=False),
)
_accounts = Table(
    "accounts",
    _metadata,
    Column("account_id", String, primary_key=True),
    Column("label", String, nullable=True),
    Column("session_name", String, nullable=True),
    Column("status", String, nullable=False),
    Column("user_id", BigInteger, nullable=True),
    Column("phone", String, nullable=True),
    Column("username", String, nullable=True),
    Column("first_name", String, nullable=True),
    Column("last_name", String, nullable=True),
    Column("bio", String, nullable=True),
    Column("last_checked_at", String, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    # Proxy pool: the one pool proxy this account uses (nullable = unassigned →
    # "—" in the UI). Many accounts → one proxy; SET NULL on proxy delete is
    # done in the repo (SQLite needs the constraint declared at create time).
    Column("proxy_id", String, ForeignKey("proxies.id"), nullable=True),
    # F5: two accounts pointing at the same .session file would race on the
    # same Telethon SQLite session DB. SQLite treats NULLs as distinct in a
    # UNIQUE index, so accounts without a custom session_name still coexist.
    Index("ix_accounts_session_name_unique", "session_name", unique=True),
)
_proxies = Table(
    "proxies",
    _metadata,
    Column("id", String, primary_key=True),
    Column("proxy_type", String, nullable=False),
    Column("host", String, nullable=False),
    Column("port", Integer, nullable=False),
    Column("username", String, nullable=True),
    Column("password", String, nullable=True),
    Column("status", String, nullable=False),
    Column("last_checked_at", String, nullable=True),
    Column("last_error", String, nullable=True),
    Column("exit_ip", String, nullable=True),
    Column("country_code", String, nullable=True),
    Column("country_name", String, nullable=True),
    Column("asn", String, nullable=True),
    Column("is_datacenter", Integer, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    # One pool entry per endpoint — adding the same host:port:type twice is a
    # no-op upsert, never a duplicate card.
    Index("ix_proxies_identity", "host", "port", "proxy_type", unique=True),
)
_logs = Table(
    "logs",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", String, nullable=False),
    Column("level", String, nullable=False),
    Column("status", String, nullable=False),
    Column("account_id", String, nullable=True),
    Column("event", String, nullable=False),
    Column("extra", String, nullable=False),
    # The Logs page + per-card panels filter by account_id ordering id DESC, and
    # the retention sweep filters created_at — both full-scan the (append-only,
    # unbounded) table without these. Mirrored in migration #23 for existing DBs.
    Index("ix_logs_account_id", "account_id", "id"),
    Index("ix_logs_created_at", "created_at"),
)
_warming_channels = Table(
    "warming_channels",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("channel", String, nullable=False, unique=True),
    Column("label", String, nullable=True),
    Column("created_at", String, nullable=False),
)
_warming_joined_channels = Table(
    "warming_joined_channels",
    _metadata,
    Column("account_id", String, primary_key=True),
    Column("channel", String, primary_key=True),
    Column("created_at", String, nullable=False),
)
_warming_settings = Table(
    "warming_settings",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("inter_account_chat", Integer, nullable=False),
    Column("reactions_enabled", Integer, nullable=False),
    Column("join_enabled", Integer, nullable=True),
    Column("enforce_readiness", Integer, nullable=True),
    Column("quiet_hours_enabled", Integer, nullable=True),
    Column("quiet_hours_start", Integer, nullable=True),
    Column("quiet_hours_end", Integer, nullable=True),
    Column("max_daily_actions", Integer, nullable=True),
    Column("gemini_api_key", String, nullable=False),
    Column("gemini_model", String, nullable=False),
    Column("openai_api_key", String, nullable=True),
    Column("openai_model", String, nullable=True),
    Column("captcha_llm_provider", String, nullable=True),
    Column("updated_at", String, nullable=False),
)
_warming_account_state = Table(
    "warming_account_state",
    _metadata,
    Column("account_id", String, ForeignKey("accounts.account_id"), primary_key=True),
    Column("state", String, nullable=False),
    Column("cycles_completed", Integer, nullable=False),
    Column("last_event", String, nullable=True),
    Column("last_cycle_at", String, nullable=True),
    Column("next_run_at", String, nullable=True),
    Column("updated_at", String, nullable=False),
    Column("last_error", String, nullable=True),
    Column("last_action", String, nullable=True),
    Column("last_channel", String, nullable=True),
    Column("heartbeat_at", String, nullable=True),
    Column("started_at", String, nullable=True),
    Column("stopped_at", String, nullable=True),
    Column("flood_wait_seconds", Integer, nullable=True),
    Column("flood_wait_until", String, nullable=True),
    Column("proxy_snapshot", String, nullable=True),
    Column("daily_actions", Integer, nullable=True),
    Column("daily_count_date", String, nullable=True),
    Column("quarantine_count", Integer, nullable=True),
    # P1.2: see schemas.warming.WarmingStateRecord.run_id.
    Column("run_id", String, nullable=True),
    # Lifecycle phase persisted between cycles — the previous-phase snapshot
    # the loop diffs against to detect transitions and fire ``phase_advanced``.
    Column("current_phase", String, nullable=True),
    Column("phase_entered_at", String, nullable=True),
    # Operator-set: account has been manually promoted out of warming into the
    # neurocomment pool. The neurocomment page's warmed-account overview reads
    # this so accounts only appear there after an explicit graduation, not on
    # crossing ``warmed_min_days`` alone. Default 0 keeps existing rows opt-in.
    Column("promoted_to_nc", Integer, nullable=False, server_default="0"),
    # Operator-chosen warming duration (days) from the start modal's slider; the
    # loop auto-completes the account once warming reaches it. NULL = no pick →
    # the board falls back to ``settings.neurocomment.warmed_min_days``.
    Column("target_days", Integer, nullable=True),
    # Operator-chosen activity persona from the start modal. server_default keeps
    # existing/inserted rows on the balanced cadence; the reader maps NULL → normal.
    Column("activity_persona", String, nullable=False, server_default="normal"),
)
_account_spam_status = Table(
    "account_spam_status",
    _metadata,
    Column("account_id", String, ForeignKey("accounts.account_id"), primary_key=True),
    Column("status", String, nullable=False),
    Column("detail", String, nullable=True),
    Column("checked_at", String, nullable=False),
)
_users = Table(
    "users",
    _metadata,
    Column("id", String, primary_key=True),
    Column("username", String, nullable=False, unique=True),
    Column("password_hash", String, nullable=False),
    # ``role`` exists from day one so RBAC can land without a migration; a single
    # role ("admin") is used until a second one is needed.
    Column("role", String, nullable=False),
    # Monotonic session-revocation counter carried in the JWT ``ver`` claim;
    # bumped on logout to invalidate every outstanding token (migration #22).
    Column("token_version", Integer, nullable=False, server_default="0"),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)
