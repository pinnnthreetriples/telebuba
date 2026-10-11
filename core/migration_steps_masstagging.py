"""Migration 73 — the masstagging tables.

``CREATE ... IF NOT EXISTS`` throughout: ``create_all`` runs BEFORE ``apply_migrations``,
so on a fresh database the schema comes from ``core.repositories.masstagging._tables``
and this is a no-op. The two spellings must agree down to the CHECK constraints —
pinned by ``tests/core/test_migrations_masstagging.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_CAMPAIGNS = (
    "CREATE TABLE IF NOT EXISTS masstagging_campaigns ("
    " campaign_id VARCHAR NOT NULL,"
    " name VARCHAR NOT NULL,"
    " mode VARCHAR DEFAULT 'stories' NOT NULL,"
    " run_status VARCHAR DEFAULT 'idle' NOT NULL,"
    " run_id VARCHAR,"
    " rest_until_unix INTEGER,"
    " started_unix INTEGER,"
    " session_unix INTEGER,"
    " finished_unix INTEGER,"
    " last_error VARCHAR,"
    " created_at VARCHAR NOT NULL,"
    " updated_at VARCHAR NOT NULL,"
    " PRIMARY KEY (campaign_id),"
    " CHECK (mode IN ('stories','priming')),"
    " CHECK (run_status IN ('idle','running','stopping','paused','done','failed')))"
)
_SETTINGS = (
    "CREATE TABLE IF NOT EXISTS masstagging_settings ("
    " campaign_id VARCHAR NOT NULL,"
    " settings_json VARCHAR DEFAULT '{}' NOT NULL,"
    " PRIMARY KEY (campaign_id),"
    " FOREIGN KEY(campaign_id) REFERENCES masstagging_campaigns (campaign_id)"
    " ON DELETE CASCADE)"
)
_ACCOUNTS = (
    "CREATE TABLE IF NOT EXISTS masstagging_accounts ("
    " campaign_id VARCHAR NOT NULL,"
    " account_id VARCHAR NOT NULL,"
    " position INTEGER DEFAULT 0 NOT NULL,"
    " PRIMARY KEY (campaign_id, account_id),"
    " FOREIGN KEY(campaign_id) REFERENCES masstagging_campaigns (campaign_id)"
    " ON DELETE CASCADE,"
    " FOREIGN KEY(account_id) REFERENCES accounts (account_id))"
)
_TARGETS = (
    "CREATE TABLE IF NOT EXISTS masstagging_targets ("
    " campaign_id VARCHAR NOT NULL,"
    " target_key VARCHAR NOT NULL,"
    " position INTEGER NOT NULL,"
    " raw VARCHAR NOT NULL,"
    " kind VARCHAR NOT NULL,"
    " assigned_account_id VARCHAR,"
    " resolved_user_id INTEGER,"
    " resolved_by VARCHAR,"
    " username VARCHAR,"
    " first_name VARCHAR DEFAULT '' NOT NULL,"
    " state VARCHAR DEFAULT 'queued' NOT NULL,"
    " reason VARCHAR,"
    " account_id VARCHAR,"
    " next_action_unix INTEGER,"
    " story_id INTEGER,"
    " updated_unix INTEGER NOT NULL,"
    " PRIMARY KEY (campaign_id, target_key),"
    " CHECK (kind IN ('username','phone','link','contact')),"
    " CHECK (state IN ('queued','primed','working','tagged','flood','skipped','failed')),"
    " FOREIGN KEY(campaign_id) REFERENCES masstagging_campaigns (campaign_id)"
    " ON DELETE CASCADE)"
)
_JOURNAL = (
    "CREATE TABLE IF NOT EXISTS masstagging_journal ("
    " id INTEGER NOT NULL,"
    " campaign_id VARCHAR NOT NULL,"
    " target_key VARCHAR NOT NULL,"
    " event VARCHAR NOT NULL,"
    " at_unix INTEGER NOT NULL,"
    " account_id VARCHAR,"
    " detail VARCHAR,"
    " PRIMARY KEY (id),"
    " CHECK (event IN ('dialog_created','tagged','flood','skipped','failed','requeued',"
    "'now','expanded')),"
    " FOREIGN KEY(campaign_id) REFERENCES masstagging_campaigns (campaign_id)"
    " ON DELETE CASCADE)"
)
_REGISTRY = (
    "CREATE TABLE IF NOT EXISTS masstagging_tagged_registry ("
    " user_id INTEGER NOT NULL,"
    " campaign_id VARCHAR NOT NULL,"
    " at_unix INTEGER NOT NULL,"
    " PRIMARY KEY (user_id))"
)
_ACCOUNT_DAY = (
    "CREATE TABLE IF NOT EXISTS masstagging_account_day ("
    " account_id VARCHAR NOT NULL,"
    " day VARCHAR NOT NULL,"
    " stories INTEGER DEFAULT 0 NOT NULL,"
    " dialogs INTEGER DEFAULT 0 NOT NULL,"
    " tags INTEGER DEFAULT 0 NOT NULL,"
    " resolves INTEGER DEFAULT 0 NOT NULL,"
    " floods_in_row INTEGER DEFAULT 0 NOT NULL,"
    " paused_until_unix INTEGER,"
    " PRIMARY KEY (account_id, day))"
)
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_mt_targets_state"
    " ON masstagging_targets (campaign_id, state, position)",
    "CREATE INDEX IF NOT EXISTS ix_mt_journal_target"
    " ON masstagging_journal (campaign_id, target_key, at_unix)",
)


def _add_masstagging_tables(connection: Connection) -> None:
    for statement in (
        _CAMPAIGNS,
        _SETTINGS,
        _ACCOUNTS,
        _TARGETS,
        _JOURNAL,
        _REGISTRY,
        _ACCOUNT_DAY,
        *_INDEXES,
    ):
        connection.exec_driver_sql(statement)
