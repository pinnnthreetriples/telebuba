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
        has_gemini_key=bool(secret.gemini_api_key),
        gemini_model=secret.gemini_model,
        gemini_max_retries=secret.gemini_max_retries,
        gemini_min_interval_seconds=secret.gemini_min_interval_seconds,
        has_openai_key=bool(secret.openai_api_key),
        openai_model=secret.openai_model,
        captcha_llm_provider=secret.captcha_llm_provider,
        updated_at=secret.updated_at,
    )


async def load_settings() -> WarmingSettings:
    return _mask_settings(await load_warming_settings())


async def save_settings(data: WarmingSettingsUpdate) -> WarmingSettings:
    # ``clear_gemini_key`` wins over ``gemini_api_key``: the UI uses the flag
    # for an explicit "wipe the stored key" gesture; passing an empty string
    # also clears it; passing ``None`` (and no flag) preserves the existing key.
    api_key: str | None = "" if data.clear_gemini_key else data.gemini_api_key
    openai_key: str | None = "" if data.clear_openai_key else data.openai_api_key

    secret = await save_warming_settings(
        inter_account_chat=data.inter_account_chat,
        reactions_enabled=data.reactions_enabled,
        join_enabled=data.join_enabled,
        enforce_readiness=data.enforce_readiness,
        gemini_api_key=api_key,
        gemini_model=data.gemini_model,
        gemini_max_retries=data.gemini_max_retries,
        gemini_min_interval_seconds=data.gemini_min_interval_seconds,
        openai_api_key=openai_key,
        openai_model=data.openai_model,
        captcha_llm_provider=data.captcha_llm_provider,
    )
    await log_event(
        "INFO",
        "warming_settings_saved",
        extra={
            "inter_account_chat": secret.inter_account_chat,
            "reactions_enabled": secret.reactions_enabled,
            "join_enabled": secret.join_enabled,
            "enforce_readiness": secret.enforce_readiness,
            "has_gemini_key": bool(secret.gemini_api_key),
            "gemini_model": secret.gemini_model,
            "gemini_max_retries": secret.gemini_max_retries,
            "gemini_min_interval_seconds": secret.gemini_min_interval_seconds,
            "has_openai_key": bool(secret.openai_api_key),
            "captcha_llm_provider": secret.captcha_llm_provider,
        },
    )
    return _mask_settings(secret)
