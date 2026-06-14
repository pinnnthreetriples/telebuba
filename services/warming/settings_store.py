"""Warming settings — load/save the singleton settings row, masking the API key.

Thin orchestration over ``core.db``: the read model masks the stored Gemini key
(presence only) so it can be shown in the UI without leaking the secret.

Named ``settings_store`` rather than ``settings`` to avoid shadowing the
``settings`` object from ``core.config`` in the package namespace (importing a
submodule binds its name onto the parent package).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.db import load_warming_settings, save_warming_settings
from core.logging import log_event
from schemas.warming import WarmingSettings

if TYPE_CHECKING:
    from schemas.warming import WarmingSettingsSecret, WarmingSettingsUpdate


def _mask_settings(secret: WarmingSettingsSecret) -> WarmingSettings:
    return WarmingSettings(
        inter_account_chat=secret.inter_account_chat,
        reactions_enabled=secret.reactions_enabled,
        join_enabled=secret.join_enabled,
        enforce_readiness=secret.enforce_readiness,
        quiet_hours_enabled=secret.quiet_hours_enabled,
        quiet_hours_start=secret.quiet_hours_start,
        quiet_hours_end=secret.quiet_hours_end,
        max_daily_actions=secret.max_daily_actions,
        has_gemini_key=bool(secret.gemini_api_key),
        gemini_model=secret.gemini_model,
        updated_at=secret.updated_at,
    )


async def load_settings() -> WarmingSettings:
    return _mask_settings(await load_warming_settings())


async def save_settings(data: WarmingSettingsUpdate) -> WarmingSettings:
    # ``clear_gemini_key`` wins over ``gemini_api_key``: the UI uses the flag
    # for an explicit "wipe the stored key" gesture; passing an empty string
    # also clears it; passing ``None`` (and no flag) preserves the existing key.
    if data.clear_gemini_key:
        api_key: str | None = ""
    else:
        api_key = data.gemini_api_key

    secret = await save_warming_settings(
        inter_account_chat=data.inter_account_chat,
        reactions_enabled=data.reactions_enabled,
        join_enabled=data.join_enabled,
        enforce_readiness=data.enforce_readiness,
        quiet_hours_enabled=data.quiet_hours_enabled,
        quiet_hours_start=data.quiet_hours_start,
        quiet_hours_end=data.quiet_hours_end,
        max_daily_actions=data.max_daily_actions,
        gemini_api_key=api_key,
        gemini_model=data.gemini_model,
    )
    await log_event(
        "INFO",
        "warming_settings_saved",
        extra={
            "inter_account_chat": secret.inter_account_chat,
            "reactions_enabled": secret.reactions_enabled,
            "join_enabled": secret.join_enabled,
            "enforce_readiness": secret.enforce_readiness,
            "quiet_hours_enabled": secret.quiet_hours_enabled,
            "max_daily_actions": secret.max_daily_actions,
            "has_gemini_key": bool(secret.gemini_api_key),
            "gemini_model": secret.gemini_model,
        },
    )
    return _mask_settings(secret)
