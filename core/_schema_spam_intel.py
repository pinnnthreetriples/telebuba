"""SQLAlchemy tables of the spam-intel ledger: attempts, per-account state, reply samples.

Its own module because ``core._schema_tables`` is near the file-size budget. Mirrors
``core.migration_steps_spam_intel`` (114): ``create_all`` builds a fresh database from THIS
module while an existing one gets the migration, and
``tests/core/test_migrations_spam_intel.py`` compares the two.

None of these tables has a foreign key to ``accounts`` on purpose: the attempts must keep
their per-proxy spacing meaning, and a stale row costs nothing. Account-scoped rows are
still deleted with the account (``core.repositories._accounts_delete``).
"""

from __future__ import annotations

from sqlalchemy import Column, Index, Integer, String, Table, text

from core._schema_tables import _metadata

# One row per real question to @SpamBot (``kind='probe'``) or per read of its dialog
# (``kind='history'``). Written when the question goes out and finished when it ends.
_spam_probe_attempts = Table(
    "spam_probe_attempts",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("account_id", String, nullable=False),
    # The account's proxy id at the time, never its address or credentials.
    Column("proxy_key", String, nullable=True),
    Column("kind", String, nullable=False, server_default=text("'probe'")),
    Column("purpose", String, nullable=False),
    Column("caller", String, nullable=False),
    Column("started_at", String, nullable=False),
    Column("finished_at", String, nullable=True),
    Column("outcome", String, nullable=False, server_default=text("'started'")),
    # An exception class name only.
    Column("error_type", String, nullable=True),
    Column("duration_ms", Integer, nullable=True),
    Index("ix_spam_probe_attempts_account", "account_id", "started_at"),
    Index("ix_spam_probe_attempts_proxy", "proxy_key", "started_at"),
    Index("ix_spam_probe_attempts_started", "started_at"),
)

# One row per account: when it may be asked again after a failure, and the last failure.
_spam_probe_state = Table(
    "spam_probe_state",
    _metadata,
    Column("account_id", String, primary_key=True),
    Column("last_attempt_at", String, nullable=True),
    Column("last_success_at", String, nullable=True),
    Column("fail_streak", Integer, nullable=False, server_default=text("0")),
    Column("next_allowed_at", String, nullable=True),
    Column("last_caller", String, nullable=True),
    Column("last_outcome", String, nullable=True),
    Column("last_error", String, nullable=True),
    Column("history_read_at", String, nullable=True),
    Column("updated_at", String, nullable=True),
)

# Redacted copies of @SpamBot's replies, one row per distinct normalised text. Not account
# scoped: the redaction removes everything that names an account.
_spam_reply_samples = Table(
    "spam_reply_samples",
    _metadata,
    Column("text_hash", String, primary_key=True),
    Column("sample", String, nullable=False),
    Column("lang", String, nullable=True),
    Column("source", String, nullable=False, server_default=text("'probe'")),
    Column("legacy_status", String, nullable=True),
    Column("strict_status", String, nullable=True),
    Column("has_date", Integer, nullable=False, server_default=text("0")),
    Column("first_seen_at", String, nullable=False),
    Column("last_seen_at", String, nullable=False),
    Column("seen_count", Integer, nullable=False, server_default=text("1")),
)
