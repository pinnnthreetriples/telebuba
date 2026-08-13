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
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import (
    create_engine,
    event,
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
from core.secure_paths import make_private_dir, make_private_file
from schemas.device_fingerprint import DeviceFingerprint, DevicePlatform

if TYPE_CHECKING:
    from collections.abc import Mapping
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
    # A new database means any cached settings row belongs to the old one.
    from core.repositories._warming_settings import (  # noqa: PLC0415 - avoids an import cycle
        _invalidate_warming_settings_cache,
    )

    _invalidate_warming_settings_cache()


def dispose_engine() -> None:
    """Release the SQLAlchemy connection pool.

    Registered via ``atexit`` so a clean process exit closes pooled
    connections and does not leak a ``ResourceWarning: unclosed database``.
    """
    if _state.engine is not None:
        _state.engine.dispose()
        _state.engine = None


atexit.register(dispose_engine)


# Guards the lazy build below. Every DB call runs in its own ``asyncio.to_thread``
# worker, so the first two after ``configure_database`` genuinely race the ``is None``
# check: unguarded, both build an engine, both run ``create_all`` + ``apply_migrations``
# against the same file at once ("database is locked"), and the loser is dropped WITHOUT
# ``dispose()`` — its pooled sqlite3 connection then trips "ResourceWarning: unclosed
# database" at some later GC, which ``filterwarnings = error`` charges to whichever test
# was running. Reentrant: the body publishes the engine before migrating, so a migration
# step that reaches back through ``_get_engine`` resolves instead of deadlocking.
_ENGINE_LOCK = threading.RLock()


def _get_engine() -> Engine:
    engine = _state.engine
    if engine is not None:
        return engine  # hot path: one unsynchronised read, exactly as before.
    with _ENGINE_LOCK:
        engine = _state.engine
        return _build_engine() if engine is None else engine


def _build_engine() -> Engine:
    """Create, configure, and publish the process engine. Callers hold ``_ENGINE_LOCK``."""
    database_path = _state.database_path or settings.db.path
    # telebuba.db is a credential store, not just application state: the proxies
    # table holds plaintext proxy passwords (``core._schema_tables``, read back
    # verbatim by ``core.repositories.proxies``) alongside every ``password_hash``.
    # SQLite creates it at the default umask (0644), so it was world-readable right
    # beside the ``sessions/`` dir hardened to 0700 — same defect, different path.
    make_private_dir(database_path.parent)
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
    # After create_all, so the file exists to be restricted.
    make_private_file(database_path)
    return engine


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


def _run_liveness_query() -> None:
    with _get_engine().connect() as connection:
        connection.exec_driver_sql("SELECT 1")


async def check_database_reachable() -> None:
    """Round-trip a trivial query, raising if the datastore cannot serve it.

    Backs the readiness probe (``services.health``). Deliberately a real
    checkout + query rather than an ``_state.engine is not None`` test: the
    failures worth reporting — a missing, locked, or corrupt SQLite file, an
    exhausted pool — all leave a configured engine looking perfectly healthy.
    """
    await asyncio.to_thread(_run_liveness_query)


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
# Periodic SQLite maintenance — split into a sibling module for the file-size
# budget and re-exported here so ``from core.db import run_db_maintenance_loop``
# (main.py) keeps working. Imported at the bottom because that module reads
# ``_get_engine`` above.
# --------------------------------------------------------------------------- #
from core.db_maintenance import (  # noqa: E402, F401
    run_db_maintenance,
    run_db_maintenance_loop,
)

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
    update_account_avatar,
    update_account_from_session_check,
    update_account_profile_snapshot,
    update_account_status,
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
    list_dialogue_pairs,
    list_recent_dialogue_messages,
    mark_message_replied,
    mark_message_unreplied,
    oldest_unreplied_for,
    pair_key,
    partners_awaiting_our_reply,
    prune_and_add_pairs,
    purge_dialogue_messages_older_than,
    recent_pair_messages,
    record_dialogue_message,
    replace_dialogue_pairs,
    try_claim_message_reply,
)
from core.repositories.logs import (  # noqa: E402, F401
    count_logs,
    insert_log_row,
    list_filtered_logs,
    list_recent_logs,
    purge_logs,
    purge_logs_older_than,
)
from core.repositories.neurocomment import (  # noqa: E402, F401
    ChannelAlreadyAssignedError,
    assign_account_to_campaign,
    bump_channel_pause,
    checkpoint_backfill,
    claim_comment,
    claim_pending_posts,
    clear_captcha_retry,
    clear_channel_pause,
    clear_join_request,
    clear_rejoin_attempts,
    clear_unconfirmed_bans,
    complete_post,
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
    enqueue_post,
    enqueue_post_bounded,
    evict_cached_decision,
    fetch_active_campaign_for_channel,
    fetch_active_campaigns_for_channels,
    fetch_campaign,
    fetch_channel_paused_until,
    fetch_comment,
    fetch_linked_group,
    fetch_readiness,
    get_listener_account_id,
    get_listener_running,
    insert_challenge,
    link_channel_to_campaign,
    list_access_lost_readiness,
    list_active_watch_channels,
    list_campaign_accounts,
    list_campaign_channels,
    list_campaign_readiness,
    list_campaigns,
    list_captcha_blocked_readiness,
    list_challenged_channels,
    list_channel_readiness,
    list_delivered_comments_since,
    list_exhausted_watch_channels,
    list_expired_channel_pauses,
    list_failed_for_channel,
    list_failed_for_channels,
    list_joined_watch_channels,
    list_linked_groups,
    list_pending_join_readiness,
    list_posted_comments_for_channel_since,
    list_posted_comments_page,
    list_posted_comments_since,
    list_silent_watch_channels,
    list_waiting_comments,
    load_neurocomment_settings,
    lookup_cached_decision,
    mark_captcha_gave_up,
    mark_comment_failed,
    mark_comment_posted,
    mark_comments_deleted,
    mark_human_skipped,
    mark_inbox_stage,
    mark_pair_banned,
    mark_rejoin_gave_up,
    mark_reply_stage,
    mark_watch_channel_join_lost,
    next_pending_attempt_unix,
    park_comment,
    prepare_backfill,
    promote_waiting_to_claimed,
    purge_neurocomment_history_older_than,
    reclaim_stale_claims,
    record_comment_msg_id,
    record_join,
    release_channel_pause,
    release_claim,
    release_post,
    remove_account_from_campaign,
    requeue_processing_posts,
    resolve_pending_outcome,
    return_claimed_posts,
    save_neurocomment_settings,
    set_comment_dispatch_stage,
    set_listener_account_id,
    set_listener_running,
    stamp_captcha_retry,
    stamp_channel_post_seen,
    stamp_join_request,
    stamp_rejoin_attempt,
    stamp_unconfirmed_ban,
    touch_comment_claim,
    unconfirmed_ban_is_countable,
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
    hand_back_warming_reservation,
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
