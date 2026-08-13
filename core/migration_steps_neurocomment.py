"""Later neurocomment migration bodies and compatibility imports.

Kept in its own module so ``core.migration_steps`` stays under the file-size
budget. Foundational schema bodies live in a cohesive sibling and are re-exported
here so the migration registry and existing imports remain unchanged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.channel_tokens import channel_fold_sql
from core.migration_steps import _sqlite_columns, _sqlite_table_exists
from core.migration_steps_neurocomment_schema import (
    _add_neurocomment_challenges,
    _add_neurocomment_comment_indexes,
    _add_neurocomment_inbox,
    _add_neurocomment_runtime,
    _add_neurocomment_settings,
    _add_neurocomment_tables,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

__all__ = [
    "_add_neurocomment_challenges",
    "_add_neurocomment_comment_indexes",
    "_add_neurocomment_inbox",
    "_add_neurocomment_runtime",
    "_add_neurocomment_settings",
    "_add_neurocomment_tables",
]

logger = logging.getLogger(__name__)

# The one-active-campaign fold. Both the index below and the duplicate SWEEP that
# guards it evaluate this same SQL, so the sweep can never demote a pair the index
# would have accepted (it would if it folded in Python: SQLite's ``lower()`` is
# ASCII-only, Python's ``str.lower()`` is not).
_CHANNEL_FOLD = channel_fold_sql("channel")
# The folded index gets its own name so it can be created BEFORE #11's is dropped.
_FOLD_INDEX = "ix_nc_channel_one_active_campaign_fold"


def _add_readiness_human_skipped(connection: Connection) -> None:
    # Ф2 #148: operator "Skip channel for this account" → a per-(account, channel)
    # human override the engine never selects. Default 0 so existing rows are unskipped.
    if "human_skipped" not in _sqlite_columns(connection, "neurocomment_readiness"):
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_readiness "
            "ADD COLUMN human_skipped INTEGER NOT NULL DEFAULT 0",
        )


def _add_readiness_banned(connection: Connection) -> None:
    # #30: auto-detected hard ban per (account, channel). Sticky so a re-onboard
    # can't flip a banned pair back to selectable. Default 0 so existing rows are
    # not banned; cleared when "Проверить каналы" sees the account can send again.
    if "banned" not in _sqlite_columns(connection, "neurocomment_readiness"):
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_readiness ADD COLUMN banned INTEGER NOT NULL DEFAULT 0",
        )


def _add_readiness_join_request(connection: Connection) -> None:
    # A discussion group behind admin approval wrote the very same
    # (joined=0, captcha_passed=0, ready=0) row the challenge back-off writes, so
    # "waiting for approval" was indistinguishable from "cooled down" and every later
    # onboarding pass re-sent the join RPC — 32 live pairs re-requested ~6 times in
    # three days. These two columns make the request visible: when the most recent one
    # went out, and how many have. NULL / 0 on existing rows = nothing outstanding, so
    # the first pass after the upgrade stamps them as if the request were new.
    columns = _sqlite_columns(connection, "neurocomment_readiness")
    if "join_requested_at" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_readiness ADD COLUMN join_requested_at VARCHAR",
        )
    if "join_request_attempts" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_readiness "
            "ADD COLUMN join_request_attempts INTEGER NOT NULL DEFAULT 0",
        )


def _add_neurocomment_comment_deleted_at(connection: Connection) -> None:
    # #27: mark a posted comment that later vanished from the channel. NULL = still
    # live; an ISO timestamp = when we noticed it was deleted. The comments table is
    # outside migration_steps._ALLOWED_TABLES, so the column probe is inlined (a
    # hard-coded table name, never user input).
    if not _sqlite_table_exists(connection, "neurocomment_comments"):
        return
    rows = connection.exec_driver_sql("PRAGMA table_info(neurocomment_comments)").mappings().all()
    if "deleted_at" not in {str(row["name"]) for row in rows}:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_comments ADD COLUMN deleted_at VARCHAR",
        )


def _add_campaign_account_channel(connection: Connection) -> None:
    # #25: optional per-account channel pin. NULL = all campaign channels (current
    # behaviour); a channel handle restricts the account to that one channel. The
    # account-link table is outside migration_steps._ALLOWED_TABLES, so the column
    # probe is inlined (a hard-coded table name, never user input) rather than routed
    # through _sqlite_columns.
    if not _sqlite_table_exists(connection, "neurocomment_campaign_accounts"):
        return
    rows = (
        connection.exec_driver_sql("PRAGMA table_info(neurocomment_campaign_accounts)")
        .mappings()
        .all()
    )
    if "channel" not in {str(row["name"]) for row in rows}:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_campaign_accounts ADD COLUMN channel VARCHAR",
        )


def _add_neurocomment_cooldowns(connection: Connection) -> None:
    # #34: durable backing for the in-memory engine cooldowns. Mirrors the
    # SQLAlchemy table in _tables; created idempotently so existing databases gain
    # it on the next engine init. channel='' = account-wide (flood/peer-flood); a
    # handle = per-channel slow-mode. until is an ISO-8601 UTC deadline.
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neurocomment_cooldowns ("
        "  account_id VARCHAR NOT NULL,"
        "  channel VARCHAR NOT NULL,"
        "  until VARCHAR NOT NULL,"
        "  PRIMARY KEY (account_id, channel)"
        ")",
    )


def _add_neurocomment_challenges_channel_index(connection: Connection) -> None:
    # #35: composite index leading with `channel` for the channel-scoped challenge
    # reads (list_failed_for_channel(s), list_challenged_channels, count_by_outcome).
    # The existing indexes lead with challenge_hash or account_id, so a channel-first
    # filter full-scans the append-only table. Column order matches those query shapes:
    # channel filter, outcome filter, decided_at sort.
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_nc_challenges_channel_outcome_decided "
        "ON neurocomment_challenges(channel, outcome, decided_at DESC)",
    )


def _add_campaign_account_channels_table(connection: Connection) -> None:
    # #29: per-account channel SUBSET within a campaign — one row per pinned channel.
    # NO rows for a (campaign, account) pair = serves ALL campaign channels (default).
    # Supersedes the scalar `channel` pin (#25); existing non-NULL pins are backfilled
    # here as a single subset row each (INSERT OR IGNORE keeps the migration idempotent).
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neurocomment_campaign_account_channels ("
        "  campaign_id VARCHAR NOT NULL REFERENCES neurocomment_campaigns(campaign_id),"
        "  account_id VARCHAR NOT NULL REFERENCES accounts(account_id),"
        "  channel VARCHAR NOT NULL,"
        "  created_at VARCHAR NOT NULL,"
        "  PRIMARY KEY (campaign_id, account_id, channel)"
        ")",
    )
    if not _sqlite_table_exists(connection, "neurocomment_campaign_accounts"):
        return
    rows = (
        connection.exec_driver_sql("PRAGMA table_info(neurocomment_campaign_accounts)")
        .mappings()
        .all()
    )
    if "channel" in {str(row["name"]) for row in rows}:
        connection.exec_driver_sql(
            "INSERT OR IGNORE INTO neurocomment_campaign_account_channels "
            "(campaign_id, account_id, channel, created_at) "
            "SELECT campaign_id, account_id, channel, created_at "
            "FROM neurocomment_campaign_accounts WHERE channel IS NOT NULL",
        )


def _add_neurocomment_join_log(connection: Connection) -> None:
    # #35: append-only per-account channel-join log backing the rolling-24h join
    # cap. Telegram freezes an account after ~20-50 channel joins a day; one row is
    # written per real join RPC that returned ok, and the (account_id, joined_at)
    # index serves the per-account window count both join sites gate on.
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neurocomment_join_log ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  account_id VARCHAR NOT NULL,"
        "  joined_at VARCHAR NOT NULL"
        ")",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_nc_join_log_account_joined "
        "ON neurocomment_join_log(account_id, joined_at)",
    )


def _add_neurocomment_join_log_watch_channel(connection: Connection) -> None:
    # #40: which watch channel a listener join subscribed to. The listener kept its
    # joined set in memory only, so every restart re-sent JoinChannel for channels it
    # was already in — Telegram answers "ok" (not already_participant) for a public
    # channel, so each no-op counted against the rolling-24h cap and starved the real
    # joins. Persisting the channel lets the pass seed its cache from the log.
    rows = connection.exec_driver_sql("PRAGMA table_info(neurocomment_join_log)").mappings().all()
    if "watch_channel" not in {str(row["name"]) for row in rows}:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_join_log ADD COLUMN watch_channel VARCHAR",
        )


def _add_neurocomment_channel_case_fold_index(connection: Connection) -> None:
    """#39: make "one active campaign per channel" case- and ``@``-insensitive.

    Telegram usernames are case-insensitive and the leading ``@`` is decoration, so
    the #11 index (``ON neurocomment_campaign_channels(channel) WHERE active = 1``)
    happily let ``Telegram`` or ``@telegram`` be linked while ``telegram`` was already
    active elsewhere. Both spellings resolve to the SAME peer id, so the listener's
    ``channel_by_peer_id`` map keeps only the last one — the other campaign's link goes
    silently dead while still looking healthy. This recreates the index over the
    ``dedup_key`` fold, so ``+HASH`` invite keys (which ARE case-sensitive) keep their
    exact-match behaviour.

    Pre-existing violators exist: a database can already hold ``telegram`` on
    campaign A and ``Telegram`` on B, both active, and ``CREATE UNIQUE INDEX`` would
    fail on it. Such a collision is resolved by DEACTIVATING the later link (the
    higher ``id``) — never by deleting it. The row survives with ``active = 0``, so
    the operator can simply re-link the channel to the campaign they meant; its
    per-account channel-subset rows are dropped exactly as ``deactivate_channel``
    drops them, because a subset entry for a non-active channel would silently
    exclude that account from selection forever. Every demotion is logged at
    WARNING, naming those accounts: an account left with NO pins serves every channel
    of its campaign, so the operator has to re-pin them.
    """
    if not _sqlite_table_exists(connection, "neurocomment_campaign_channels"):
        return
    subsets_exist = _sqlite_table_exists(connection, "neurocomment_campaign_account_channels")
    active_links = connection.exec_driver_sql(
        # _CHANNEL_FOLD is a module constant from channel_fold_sql(); nothing here is
        # caller-supplied. Interpolating it is the point — the sweep must fold exactly
        # as the index does, and spelling the expression out a second time is what
        # caused the drift this migration exists to fix.
        f"SELECT id, campaign_id, channel, {_CHANNEL_FOLD} AS fold "  # noqa: S608 # nosec B608
        "FROM neurocomment_campaign_channels WHERE active = 1 ORDER BY id",
    ).all()
    seen: set[str] = set()
    for link_id, campaign_id, channel, fold in active_links:
        if str(fold) not in seen:
            seen.add(str(fold))
            continue
        connection.exec_driver_sql(
            "UPDATE neurocomment_campaign_channels SET active = 0 WHERE id = ?",
            (link_id,),
        )
        unpinned: list[str] = []
        if subsets_exist:
            unpinned = [
                str(row[0])
                for row in connection.exec_driver_sql(
                    "SELECT account_id FROM neurocomment_campaign_account_channels "
                    "WHERE campaign_id = ? AND channel = ? ORDER BY account_id",
                    (campaign_id, channel),
                ).all()
            ]
            connection.exec_driver_sql(
                "DELETE FROM neurocomment_campaign_account_channels "
                "WHERE campaign_id = ? AND channel = ?",
                (campaign_id, channel),
            )
        logger.warning(
            "migration 39: deactivated case-duplicate channel link %r in campaign %r "
            "(link id %s) — the same channel was already active under a different spelling; "
            "re-link it if this was the campaign you wanted. Accounts that lost their pin "
            "on it (an account with no pins left serves EVERY channel of the campaign, so "
            "re-pin them): %s",
            channel,
            campaign_id,
            link_id,
            ", ".join(unpinned) or "none",
        )
    # Create the folded index under its OWN name before dropping #11's, so there is no
    # window in which nothing constrains the table: pysqlite emits no BEGIN ahead of
    # DDL, so with no duplicates to sweep the DROP would run in autocommit and survive
    # both a failing CREATE and the registry's abort, leaving the invariant unenforced.
    connection.exec_driver_sql(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_FOLD_INDEX} "
        f"ON neurocomment_campaign_channels({_CHANNEL_FOLD}) WHERE active = 1",
    )
    connection.exec_driver_sql("DROP INDEX IF EXISTS ix_neurocomment_channel_one_active_campaign")
