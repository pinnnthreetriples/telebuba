"""Tiny versioned SQLite migration registry.

Every schema change is a numbered, named migration that runs at most once per DB.
State lives in ``schema_version`` (one row per applied migration). Constraints:
no Alembic; each migration MUST be idempotent (``ADD COLUMN`` guarded by a
column-name check); append-only — never edit a migration in place.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.migration_steps import (
    _add_account_avatar,
    _add_account_bio,
    _add_account_premium,
    _add_account_proxy_geo,
    _add_account_twofa_password,
    _add_logs_indexes,
    _add_neurocomment_challenges,
    _add_neurocomment_comment_indexes,
    _add_neurocomment_runtime,
    _add_neurocomment_settings,
    _add_neurocomment_tables,
    _add_readiness_banned,
    _add_readiness_human_skipped,
    _add_unique_session_name_index,
    _add_users_table,
    _add_users_token_version,
    _add_warming_join_enabled,
    _add_warming_joined_channels,
    _add_warming_phase_columns,
    _add_warming_settings_gemini_tuning,
    _add_warming_settings_llm_columns,
    _add_warming_state_nc_handed_off,
    _add_warming_state_promoted_to_nc,
    _add_warming_state_reservation_token,
    _add_warming_state_run_id,
    _add_warming_state_runtime_columns,
    _add_warming_state_target_days,
    _add_warming_user_controls,
    _rename_proxy_type_http_to_https,
)
from core.migration_steps_access_reason import _add_readiness_access_lost_reason
from core.migration_steps_account_limits import _add_neurocomment_account_limits
from core.migration_steps_budget_reset import _reset_overshot_retry_budgets
from core.migration_steps_captcha_giveup import _add_readiness_captcha_giveup
from core.migration_steps_channel_activity import _add_campaign_channel_last_post
from core.migration_steps_channel_pause import _add_campaign_channel_pause
from core.migration_steps_comment_mode import _add_neurocomment_settings_comment_mode
from core.migration_steps_discovery import (
    _add_discovery_kind_and_seen,
    _add_linked_group_about,
    _add_neurocomment_discovery_candidates,
    _add_warming_settings_telemetr_key,
)
from core.migration_steps_join_lost import _add_neurocomment_join_log_lost_at
from core.migration_steps_neurocomment import (
    _add_campaign_account_channel,
    _add_campaign_account_channels_table,
    _add_neurocomment_challenges_channel_index,
    _add_neurocomment_channel_case_fold_index,
    _add_neurocomment_comment_deleted_at,
    _add_neurocomment_cooldowns,
    _add_neurocomment_inbox,
    _add_neurocomment_join_log,
    _add_neurocomment_join_log_watch_channel,
    _add_readiness_join_request,
)
from core.migration_steps_neuroshilling import _add_neuroshilling_tables
from core.migration_steps_neuroshilling_chat import _add_neuroshilling_chat_log
from core.migration_steps_pool import (
    _add_neurocomment_listener_running,
    _add_proxy_geo_consensus,
    _add_proxy_pool,
    _add_warming_state_activity_persona,
)
from core.migration_steps_proxy_hardening import _harden_proxy_hosts
from core.migration_steps_rejoin import _add_readiness_rejoin, _add_readiness_rejoin_gave_up
from core.migration_steps_unconfirmed_ban import _add_readiness_unconfirmed_ban

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Connection, Engine

    _Migration = Callable[[Connection], None]


# Append-only registry. ``version`` is the canonical identifier and must never
# be reused; ``name`` is informational and surfaces in the audit table.
MIGRATIONS: tuple[tuple[int, str, _Migration], ...] = (
    (1, "add_account_bio", _add_account_bio),
    (2, "add_account_proxy_geo", _add_account_proxy_geo),
    (3, "add_warming_state_runtime_columns", _add_warming_state_runtime_columns),
    (4, "add_warming_join_enabled", _add_warming_join_enabled),
    (5, "add_warming_user_controls", _add_warming_user_controls),
    (6, "add_warming_joined_channels", _add_warming_joined_channels),
    (7, "add_unique_session_name_index", _add_unique_session_name_index),
    (8, "add_warming_state_run_id", _add_warming_state_run_id),
    (9, "rename_proxy_type_http_to_https", _rename_proxy_type_http_to_https),
    (10, "add_warming_phase_columns", _add_warming_phase_columns),
    (11, "add_neurocomment_tables", _add_neurocomment_tables),
    (12, "add_neurocomment_runtime", _add_neurocomment_runtime),
    (13, "add_neurocomment_comment_indexes", _add_neurocomment_comment_indexes),
    (14, "add_neurocomment_challenges", _add_neurocomment_challenges),
    (15, "add_readiness_human_skipped", _add_readiness_human_skipped),
    (16, "add_warming_state_promoted_to_nc", _add_warming_state_promoted_to_nc),
    (17, "add_users_table", _add_users_table),
    (18, "add_proxy_pool", _add_proxy_pool),
    (19, "add_neurocomment_settings", _add_neurocomment_settings),
    (20, "add_warming_state_target_days", _add_warming_state_target_days),
    (21, "add_warming_state_activity_persona", _add_warming_state_activity_persona),
    (22, "add_users_token_version", _add_users_token_version),
    (23, "add_logs_indexes", _add_logs_indexes),
    (24, "add_neurocomment_listener_running", _add_neurocomment_listener_running),
    (25, "add_campaign_account_channel", _add_campaign_account_channel),
    (26, "add_warming_settings_llm_columns", _add_warming_settings_llm_columns),
    (27, "add_neurocomment_comment_deleted_at", _add_neurocomment_comment_deleted_at),
    (28, "add_warming_settings_gemini_tuning", _add_warming_settings_gemini_tuning),
    (29, "add_campaign_account_channels_table", _add_campaign_account_channels_table),
    (30, "add_readiness_banned", _add_readiness_banned),
    (31, "add_warming_state_nc_handed_off", _add_warming_state_nc_handed_off),
    (32, "add_account_avatar", _add_account_avatar),
    (33, "add_proxy_geo_consensus", _add_proxy_geo_consensus),
    (34, "add_neurocomment_cooldowns", _add_neurocomment_cooldowns),
    (
        35,
        "add_neurocomment_challenges_channel_index",
        _add_neurocomment_challenges_channel_index,
    ),
    (36, "add_neurocomment_join_log", _add_neurocomment_join_log),
    (37, "add_warming_settings_telemetr_key", _add_warming_settings_telemetr_key),
    (
        38,
        "add_neurocomment_discovery_candidates",
        _add_neurocomment_discovery_candidates,
    ),
    (
        39,
        "add_neurocomment_channel_case_fold_index",
        _add_neurocomment_channel_case_fold_index,
    ),
    (
        40,
        "add_neurocomment_join_log_watch_channel",
        _add_neurocomment_join_log_watch_channel,
    ),
    (41, "add_readiness_join_request", _add_readiness_join_request),
    (42, "add_campaign_channel_pause", _add_campaign_channel_pause),
    (43, "add_readiness_rejoin", _add_readiness_rejoin),
    (44, "add_readiness_access_lost_reason", _add_readiness_access_lost_reason),
    (
        45,
        "add_neurocomment_join_log_lost_at",
        _add_neurocomment_join_log_lost_at,
    ),
    (
        46,
        "add_warming_state_reservation_token",
        _add_warming_state_reservation_token,
    ),
    (47, "add_readiness_unconfirmed_ban", _add_readiness_unconfirmed_ban),
    (48, "reset_overshot_retry_budgets", _reset_overshot_retry_budgets),
    (49, "add_readiness_captcha_giveup", _add_readiness_captcha_giveup),
    (50, "add_readiness_rejoin_gave_up", _add_readiness_rejoin_gave_up),
    (51, "add_campaign_channel_last_post", _add_campaign_channel_last_post),
    (
        52,
        "add_neurocomment_settings_comment_mode",
        _add_neurocomment_settings_comment_mode,
    ),
    (53, "add_neurocomment_inbox", _add_neurocomment_inbox),
    (54, "harden_proxy_hosts", _harden_proxy_hosts),
    (55, "add_neuroshilling_tables", _add_neuroshilling_tables),
    (56, "add_neuroshilling_chat_log", _add_neuroshilling_chat_log),
    (57, "add_account_twofa_password", _add_account_twofa_password),
    (58, "add_neurocomment_account_limits", _add_neurocomment_account_limits),
    (59, "add_account_premium", _add_account_premium),
    (60, "add_discovery_kind_and_seen", _add_discovery_kind_and_seen),
    (61, "add_linked_group_about", _add_linked_group_about),
)


def _ensure_schema_version_table(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version INTEGER PRIMARY KEY,"
        "  name VARCHAR NOT NULL,"
        "  applied_at VARCHAR NOT NULL"
        ")",
    )


def _applied_versions(connection: Connection) -> set[int]:
    rows = connection.exec_driver_sql("SELECT version FROM schema_version").all()
    return {int(row[0]) for row in rows}


def apply_migrations(engine: Engine) -> None:
    """Run every migration that has not yet been stamped on this database.

    Idempotent: a fresh database where ``create_all`` already produced every
    column still gets each migration row inserted (the ``ADD COLUMN`` guards
    make the bodies no-ops). Re-running the same registry on the same DB does
    nothing.
    """
    with engine.begin() as connection:
        _ensure_schema_version_table(connection)
        applied = _applied_versions(connection)
        for version, name, body in MIGRATIONS:
            if version in applied:
                continue
            body(connection)
            connection.exec_driver_sql(
                "INSERT INTO schema_version(version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, datetime.now(UTC).isoformat()),
            )
