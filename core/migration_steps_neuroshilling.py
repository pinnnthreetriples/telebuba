"""Neuroshilling schema migration body — the six tables of the domain.

One private helper per table (plus its indexes) so no body approaches the
function-size budget and a later change to one table touches one helper.

Everything is ``CREATE ... IF NOT EXISTS``: ``core.db._build_engine`` runs
``create_all`` BEFORE ``apply_migrations``, so on a fresh database the real
schema comes from ``core.repositories.neuroshilling._tables`` and every
statement here is a no-op. The two spellings must therefore agree down to the
CHECK constraints and foreign keys — pinned by
``tests/core/test_migrations_neuroshilling.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_neuroshilling_tables(connection: Connection) -> None:
    """Create the neuroshilling domain schema (six tables + their indexes)."""
    _ns_campaigns(connection)
    _ns_roles(connection)
    _ns_accounts(connection)
    _ns_steps(connection)
    _ns_presence(connection)
    _ns_messages(connection)


def _ns_campaigns(connection: Connection) -> None:
    """The campaign row: scenario state, pacing knobs, quota ceilings, run state.

    ``run_id`` is the current run and is READ on resume rather than re-minted —
    a fresh id would empty the journal's unique index and replay the whole
    staged dialogue into chats that already hold it. ``last_error`` carries a
    class NAME, never ``str(exc)``: this row is served back by the API.
    """
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neuroshilling_campaigns ("
        " campaign_id VARCHAR NOT NULL PRIMARY KEY,"
        " name VARCHAR NOT NULL,"
        " mode VARCHAR NOT NULL DEFAULT 'campaign'"
        " CHECK (mode IN ('campaign','revive')),"
        " topic VARCHAR NOT NULL DEFAULT '',"
        " targets_raw VARCHAR NOT NULL DEFAULT '',"
        " unique_messages INTEGER NOT NULL DEFAULT 1,"
        " use_chat_context INTEGER NOT NULL DEFAULT 0,"
        " media_message_link VARCHAR,"
        " media_step_position INTEGER,"
        " scenario_status VARCHAR NOT NULL DEFAULT 'draft'"
        " CHECK (scenario_status IN ('draft','approved')),"
        " run_mode VARCHAR NOT NULL DEFAULT 'sequential'"
        " CHECK (run_mode IN ('sequential','parallel')),"
        " pause_min_seconds INTEGER NOT NULL DEFAULT 10,"
        " pause_max_seconds INTEGER NOT NULL DEFAULT 20,"
        " messages_per_hour INTEGER NOT NULL DEFAULT 10,"
        " messages_per_chat_per_day INTEGER NOT NULL DEFAULT 3,"
        " total_per_account INTEGER,"
        " reserve_enabled INTEGER NOT NULL DEFAULT 0,"
        " autoresponder VARCHAR NOT NULL DEFAULT 'off'"
        " CHECK (autoresponder IN ('off','neurodialog')),"
        " reply_to_humans INTEGER NOT NULL DEFAULT 0,"
        " reply_activity VARCHAR NOT NULL DEFAULT 'medium'"
        " CHECK (reply_activity IN ('calm','medium','active')),"
        " listen_minutes INTEGER NOT NULL DEFAULT 60,"
        " status VARCHAR NOT NULL DEFAULT 'idle'"
        " CHECK (status IN ('idle','running','stopping','done','failed')),"
        " run_id VARCHAR,"
        " last_error VARCHAR,"
        " created_at VARCHAR NOT NULL,"
        " updated_at VARCHAR NOT NULL)",
    )


def _ns_roles(connection: Connection) -> None:
    """A campaign persona. ``description`` IS the persona prompt, so it is generous.

    No colour and no position column: the colour is a design token the client
    picks by index, and the order of roles is their creation order.
    """
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neuroshilling_roles ("
        " role_id VARCHAR NOT NULL PRIMARY KEY,"
        " campaign_id VARCHAR NOT NULL"
        " REFERENCES neuroshilling_campaigns(campaign_id) ON DELETE CASCADE,"
        " name VARCHAR NOT NULL,"
        " description VARCHAR NOT NULL DEFAULT '',"
        " created_at VARCHAR NOT NULL)",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_ns_roles_campaign "
        "ON neuroshilling_roles(campaign_id, created_at)",
    )


def _ns_accounts(connection: Connection) -> None:
    """Which accounts a campaign may play, and as which role.

    The primary key is the (campaign, account) PAIR on purpose: one account may
    legitimately be ASSIGNED to several campaigns, and the exclusion that matters
    — only one RUNNING campaign may hold it — is enforced at start time by the
    in-memory ownership registry, not by the schema.
    """
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neuroshilling_accounts ("
        " campaign_id VARCHAR NOT NULL"
        " REFERENCES neuroshilling_campaigns(campaign_id) ON DELETE CASCADE,"
        " account_id VARCHAR NOT NULL REFERENCES accounts(account_id),"
        " role_id VARCHAR REFERENCES neuroshilling_roles(role_id) ON DELETE SET NULL,"
        " is_reserve INTEGER NOT NULL DEFAULT 0,"
        " state VARCHAR NOT NULL DEFAULT 'active'"
        " CHECK (state IN ('active','banned','replaced')),"
        " replaced_by_account_id VARCHAR,"
        " created_at VARCHAR NOT NULL,"
        " PRIMARY KEY (campaign_id, account_id))",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_ns_accounts_role "
        "ON neuroshilling_accounts(campaign_id, role_id)",
    )


def _ns_steps(connection: Connection) -> None:
    """One line of the staged dialogue, ordered by ``position`` within a campaign.

    ``reply_to_position`` and ``target_position`` point at OTHER positions rather
    than at step ids, so a regenerated scenario keeps its shape. Which step
    carries the campaign's media is a single ``campaigns.media_step_position``,
    not a flag here — one field is cheaper than a flag plus a uniqueness rule.
    """
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neuroshilling_steps ("
        " step_id VARCHAR NOT NULL PRIMARY KEY,"
        " campaign_id VARCHAR NOT NULL"
        " REFERENCES neuroshilling_campaigns(campaign_id) ON DELETE CASCADE,"
        " position INTEGER NOT NULL CHECK (position >= 1),"
        " kind VARCHAR NOT NULL CHECK (kind IN ('message','reaction')),"
        " role_id VARCHAR REFERENCES neuroshilling_roles(role_id) ON DELETE SET NULL,"
        " text VARCHAR NOT NULL DEFAULT '',"
        " reply_to_position INTEGER,"
        " target_position INTEGER,"
        " emoji VARCHAR,"
        " delay_min_seconds INTEGER NOT NULL DEFAULT 60"
        " CHECK (delay_min_seconds BETWEEN 0 AND 3600),"
        " delay_max_seconds INTEGER NOT NULL DEFAULT 180"
        " CHECK (delay_max_seconds BETWEEN 0 AND 3600),"
        " created_at VARCHAR NOT NULL,"
        " updated_at VARCHAR NOT NULL)",
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_ns_steps_position "
        "ON neuroshilling_steps(campaign_id, position)",
    )


def _ns_presence(connection: Connection) -> None:
    """Whether a given account is inside a given target chat.

    Joining is a property of the PAIR, not of the target: a numeric chat id is
    resolved through the session's own entity cache and each account keeps its
    own session file, so an id one account resolved is useless to another. The
    only cure for a private supergroup is for each account to actually join, and
    the five distinguishable join outcomes live in ``state``.

    No ``chat_id`` column on purpose — each account re-resolves its own after
    joining, and a restart costs one RPC per pair.
    """
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neuroshilling_presence ("
        " campaign_id VARCHAR NOT NULL"
        " REFERENCES neuroshilling_campaigns(campaign_id) ON DELETE CASCADE,"
        " account_id VARCHAR NOT NULL,"
        " target VARCHAR NOT NULL,"
        " state VARCHAR NOT NULL DEFAULT 'pending'"
        " CHECK (state IN"
        " ('pending','joined','pending_approval','refused','flooded','retired')),"
        " last_error_type VARCHAR,"
        " joined_at VARCHAR,"
        " updated_at VARCHAR NOT NULL,"
        " PRIMARY KEY (campaign_id, account_id, target))",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_ns_presence_target "
        "ON neuroshilling_presence(campaign_id, target, state)",
    )


def _ns_messages(connection: Connection) -> None:
    """The send journal: progress numerator, quota counter and replay guard in one.

    A row is INSERTed ``pending`` BEFORE the send and only then updated, mirroring
    ``neurocomment_comments``: a unique index protects rows that exist, so a crash
    between sending and recording would otherwise replay into a stranger's chat.

    ``ix_ns_messages_account_created`` is keyed on ``created_at`` and not
    ``sent_at`` because the quota predicate counts ``status IN ('pending','sent')``
    and a pending row has no ``sent_at`` yet.
    """
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neuroshilling_messages ("
        " id INTEGER NOT NULL PRIMARY KEY,"
        " campaign_id VARCHAR NOT NULL"
        " REFERENCES neuroshilling_campaigns(campaign_id) ON DELETE CASCADE,"
        " run_id VARCHAR NOT NULL,"
        " target VARCHAR NOT NULL,"
        " step_id VARCHAR NOT NULL REFERENCES neuroshilling_steps(step_id),"
        " account_id VARCHAR NOT NULL,"
        " text VARCHAR NOT NULL DEFAULT '',"
        " message_id INTEGER,"
        " status VARCHAR NOT NULL"
        " CHECK (status IN ('pending','sent','failed','skipped')),"
        " error_type VARCHAR,"
        " sent_at VARCHAR,"
        " created_at VARCHAR NOT NULL)",
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_ns_messages_step "
        "ON neuroshilling_messages(run_id, target, step_id)",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_ns_messages_account_created "
        "ON neuroshilling_messages(account_id, created_at)",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_ns_messages_chat_day "
        "ON neuroshilling_messages(account_id, target, created_at)",
    )
