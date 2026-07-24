"""Shared SQLite plumbing — schema, engine, generic helpers.

This module owns the SQLAlchemy ``MetaData``, every table definition, engine
lifecycle, and the small row/value helpers shared across aggregates. Schema
evolution is delegated to :mod:`core.migrations` — ``_get_engine`` calls
``apply_migrations`` after ``create_all`` so every unstamped migration runs
once. The per-aggregate query functions live in
``core/repositories/<aggregate>.py`` (split out for #38); they import the
table objects and helpers below, and this module re-exports their public
functions at the bottom so existing ``from core.db import ...`` call sites
keep working.
"""

from __future__ import annotations

import asyncio
import atexit
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import (
    create_engine,
    event,
    text,
)

# Schema (MetaData + every table) lives in a sibling module for the file-size
# budget; imported back here so ``from core.db import _accounts`` etc. and the
# repositories that read these table objects keep working unchanged.
from core._schema_tables import (  # noqa: F401 - re-exported for existing import sites.
    _account_spam_status,
    _accounts,
    _device_fingerprints,
    _logs,
    _metadata,
    _proxies,
    _users,
    _warming_account_state,
    _warming_channels,
    _warming_joined_channels,
    _warming_settings,
)
from core.config import settings
from core.migrations import apply_migrations
from schemas.device_fingerprint import DeviceFingerprint, DevicePlatform

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from sqlalchemy.engine import Engine


class _DatabaseState:
    engine: Engine | None = None
    database_path: Path | None = None


_state = _DatabaseState()


def configure_database(database_path: Path) -> None:
    if _state.engine is not None:
        _state.engine.dispose()
    _state.database_path = database_path
    _state.engine = None


def dispose_engine() -> None:
    """Release the SQLAlchemy connection pool.

    Registered via ``atexit`` so a clean process exit closes pooled
    connections and does not leak a ``ResourceWarning: unclosed database``.
    """
    if _state.engine is not None:
        _state.engine.dispose()
        _state.engine = None


atexit.register(dispose_engine)


# --------------------------------------------------------------------------- #
# Periodic SQLite maintenance — WAL checkpoint + optional online backup.
# WAL never truncates on its own under a long-lived pool, and telebuba.db is the
# sole datastore (incl. users/auth), so nothing otherwise guards against loss.
# The clock is injectable so the backup filename is deterministic under test.
# --------------------------------------------------------------------------- #
_BACKUP_STEM = "telebuba"
_BACKUP_SUFFIX = ".db"
_BACKUP_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"


def _default_backup_clock() -> datetime:
    return datetime.now(UTC)


def run_db_maintenance(*, clock: Callable[[], datetime] = _default_backup_clock) -> Path | None:
    """Checkpoint the WAL and, when enabled, write + prune a timestamped backup.

    Returns the backup file path when one was written, else ``None``. The
    ``PRAGMA wal_checkpoint(TRUNCATE)`` always runs; the ``VACUUM INTO`` backup
    is gated on ``settings.db.backup_enabled``.
    """
    engine = _get_engine()
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        if not settings.db.backup_enabled:
            return None
        backup_dir = settings.db.backup_dir
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = clock().strftime(_BACKUP_TIMESTAMP_FORMAT)
        target = backup_dir / f"{_BACKUP_STEM}-{stamp}{_BACKUP_SUFFIX}"
        # VACUUM INTO copies a consistent snapshot without holding a long lock;
        # the path is a bound parameter, never interpolated SQL.
        connection.execute(text("VACUUM INTO :path"), {"path": str(target)})
    _prune_backups(backup_dir)
    return target


def _prune_backups(backup_dir: Path) -> None:
    backups = sorted(backup_dir.glob(f"{_BACKUP_STEM}-*{_BACKUP_SUFFIX}"))
    excess = len(backups) - settings.db.backup_keep
    for stale in backups[:excess]:
        stale.unlink(missing_ok=True)


async def run_db_maintenance_loop() -> None:
    """Run :func:`run_db_maintenance` on the configured interval until cancelled."""
    interval_seconds = settings.db.backup_interval_hours * 3600.0
    while True:
        await asyncio.sleep(interval_seconds)
        await asyncio.to_thread(run_db_maintenance)


def _get_engine() -> Engine:
    if _state.engine is None:
        database_path = _state.database_path or settings.db.path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False},
            pool_size=settings.db.pool_size,
            max_overflow=settings.db.max_overflow,
            pool_timeout=settings.db.pool_timeout_seconds,
            future=True,
        )

        # SQLite ignores ForeignKey constraints unless PRAGMA foreign_keys is
        # set on every connection. WAL + busy_timeout + synchronous=NORMAL let
        # concurrent warming loops write without "database is locked".
        @event.listens_for(engine, "connect")
        def _configure_sqlite(dbapi_connection: Any, _connection_record: object) -> None:  # noqa: ANN401 - SQLAlchemy hands us the raw DBAPI handle.
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        _state.engine = engine
        _metadata.create_all(engine)
        apply_migrations(engine)
    return _state.engine


# --------------------------------------------------------------------------- #
# Generic row/value helpers shared by the repositories below.
# --------------------------------------------------------------------------- #
def _row_to_device_fingerprint(mapping: Mapping[str, object]) -> DeviceFingerprint:
    return DeviceFingerprint(
        account_id=str(mapping["account_id"]),
        platform=cast("DevicePlatform", mapping["platform"]),
        device_model=str(mapping["device_model"]),
        system_version=str(mapping["system_version"]),
        app_version=str(mapping["app_version"]),
        lang_code=str(mapping["lang_code"]),
        system_lang_code=str(mapping["system_lang_code"]),
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _required_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    msg = f"Expected integer-compatible value, got {type(value).__name__}"
    raise TypeError(msg)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(cast("int | str", value))


# --------------------------------------------------------------------------- #
# Domain repositories (#38) — split out of this module and re-exported so that
# existing ``from core.db import ...`` call sites keep working unchanged. These
# imports live at the bottom because the repositories import the table objects
# and helpers defined above.
# --------------------------------------------------------------------------- #
from core.repositories.accounts import (  # noqa: E402, F401
    DuplicateSessionNameError,
    account_summary_counts,
    create_account,
    delete_account,
    fetch_account,
    fetch_account_avatar,
    list_accounts,
    list_accounts_by_ids,
    update_account_from_session_check,
    update_account_profile_snapshot,
)
from core.repositories.content import (  # noqa: E402, F401
    purge_sent_hashes_older_than,
    record_sent_hash,
    release_sent_hash,
    try_reserve_sent_hash,
    was_hash_sent_since,
)
from core.repositories.device_fingerprint import (  # noqa: E402, F401
    fetch_device_fingerprint,
    insert_device_fingerprint,
    list_device_fingerprints,
    list_device_fingerprints_by_ids,
)
from core.repositories.dialogues import (  # noqa: E402, F401
    count_pair_messages_since,
    latest_unreplied_for,
    list_dialogue_pairs,
    list_recent_dialogue_messages,
    mark_message_replied,
    mark_message_unreplied,
    pair_key,
    purge_dialogue_messages_older_than,
    record_dialogue_message,
    replace_dialogue_pairs,
    try_claim_message_reply,
)
from core.repositories.logs import (  # noqa: E402, F401
    insert_log_row,
    list_filtered_logs,
    list_recent_logs,
    purge_logs,
    purge_logs_older_than,
)
from core.repositories.neurocomment import (  # noqa: E402, F401
    ChannelAlreadyAssignedError,
    assign_account_to_campaign,
    claim_comment,
    clear_pair_banned,
    count_account_channel_comments_since,
    count_account_comments_since,
    count_account_joins_since,
    count_by_outcome,
    count_channel_comments_per_account_since,
    count_comments_per_account_since,
    create_campaign,
    deactivate_channel,
    delete_campaign,
    delete_readiness,
    fetch_active_campaign_for_channel,
    fetch_active_campaigns_for_channels,
    fetch_campaign,
    fetch_comment,
    fetch_linked_group,
    fetch_readiness,
    get_listener_account_id,
    get_listener_running,
    insert_challenge,
    link_channel_to_campaign,
    list_active_watch_channels,
    list_campaign_accounts,
    list_campaign_channels,
    list_campaign_readiness,
    list_campaigns,
    list_challenged_channels,
    list_failed_for_channel,
    list_linked_groups,
    list_posted_comments_for_channel_since,
    list_posted_comments_page,
    list_posted_comments_since,
    load_neurocomment_settings,
    lookup_cached_decision,
    mark_comment_failed,
    mark_comment_posted,
    mark_comments_deleted,
    mark_human_skipped,
    mark_pair_banned,
    reclaim_stale_claims,
    record_join,
    remove_account_from_campaign,
    resolve_pending_outcome,
    save_neurocomment_settings,
    set_listener_account_id,
    set_listener_running,
    update_campaign_prompt,
    update_solver_enabled,
    upsert_linked_group,
    upsert_readiness,
)
from core.repositories.proxies import (  # noqa: E402, F401
    ProxyCapacityError,
    assign_account_to_proxy,
    create_proxy,
    delete_proxy,
    fetch_account_proxy_settings,
    fetch_proxy,
    fetch_proxy_settings,
    list_account_ids_for_proxy,
    list_proxies,
    unassign_account_from_proxy,
    update_proxy_check,
)
from core.repositories.spam_status import (  # noqa: E402, F401
    get_spam_status,
    list_spam_statuses,
    list_spam_statuses_by_ids,
    upsert_spam_status,
)
from core.repositories.warming import (  # noqa: E402, F401
    add_warming_channel,
    fetch_warming_state,
    list_warming_account_ids,
    list_warming_channels,
    list_warming_states,
    list_warming_states_by_ids,
    load_warming_settings,
    mark_nc_handed_off,
    mark_promoted_to_nc,
    remove_warming_channel,
    save_warming_settings,
    unmark_promoted_to_nc,
    upsert_warming_state,
)
from core.repositories.warming_joined import (  # noqa: E402, F401
    is_channel_joined,
    record_channel_joined,
)
