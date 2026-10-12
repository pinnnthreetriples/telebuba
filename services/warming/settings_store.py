"""Warming settings — load/save the singleton settings row, masking the API key.

Thin orchestration over ``core.db``: the read model masks the stored LLM keys
(presence + a short hint) so they can be shown in the UI without leaking the secret.

Named ``settings_store`` rather than ``settings`` to avoid shadowing the
``settings`` object from ``core.config`` in the package namespace (importing a
submodule binds its name onto the parent package).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.db import load_warming_settings, save_warming_settings
from core.logging import log_event
from core.repositories.warming_dm_pause import store_dm_paused_until
from schemas.warming import WarmingSettings
from services.warming._dm_breaker import active_dm_pause

if TYPE_CHECKING:
    from schemas.warming import WarmingSettingsSecret, WarmingSettingsUpdate


# Below this length the first-and-last-four hint would reveal most of the key.
_HINT_MIN_KEY_LENGTH = 12


def _key_hint(key: str) -> str | None:
    """Enough of a key to recognise it — never enough to use it."""
    if not key:
        return None
    if len(key) < _HINT_MIN_KEY_LENGTH:
        return "••••"
    return f"{key[:4]}…{key[-4:]}"


def _mask_settings(secret: WarmingSettingsSecret) -> WarmingSettings:
    return WarmingSettings(
        inter_account_chat=secret.inter_account_chat,
        reactions_enabled=secret.reactions_enabled,
        join_enabled=secret.join_enabled,
        enforce_readiness=secret.enforce_readiness,
        has_gemini_key=bool(secret.gemini_api_key),
        gemini_key_hint=_key_hint(secret.gemini_api_key),
        gemini_model=secret.gemini_model,
        gemini_max_retries=secret.gemini_max_retries,
        gemini_min_interval_seconds=secret.gemini_min_interval_seconds,
        text_llm_provider=secret.text_llm_provider,
        has_openai_key=bool(secret.openai_api_key),
        openai_key_hint=_key_hint(secret.openai_api_key),
        openai_model=secret.openai_model,
        captcha_llm_provider=secret.captcha_llm_provider,
        has_deepseek_key=bool(secret.deepseek_api_key),
        deepseek_key_hint=_key_hint(secret.deepseek_api_key),
        extra_toggles=secret.extra_toggles,
        dm_paused_until=_active_pause_iso(secret.dm_paused_until),
        updated_at=secret.updated_at,
    )


def _active_pause_iso(stamp: str | None) -> str | None:
    until = active_dm_pause(stamp, datetime.now(UTC))
    return until.isoformat() if until is not None else None


async def load_settings() -> WarmingSettings:
    return _mask_settings(await load_warming_settings())


async def save_settings(data: WarmingSettingsUpdate) -> WarmingSettings:
    # ``clear_gemini_key`` wins over ``gemini_api_key``: the UI uses the flag
    # for an explicit "wipe the stored key" gesture; passing an empty string
    # also clears it; passing ``None`` (and no flag) preserves the existing key.
    api_key: str | None = "" if data.clear_gemini_key else data.gemini_api_key
    openai_key: str | None = "" if data.clear_openai_key else data.openai_api_key
    deepseek_key: str | None = "" if data.clear_deepseek_key else data.deepseek_api_key

    secret = await save_warming_settings(
        inter_account_chat=data.inter_account_chat,
        reactions_enabled=data.reactions_enabled,
        join_enabled=data.join_enabled,
        enforce_readiness=data.enforce_readiness,
        gemini_api_key=api_key,
        gemini_model=data.gemini_model,
        gemini_max_retries=data.gemini_max_retries,
        gemini_min_interval_seconds=data.gemini_min_interval_seconds,
        text_llm_provider=data.text_llm_provider,
        openai_api_key=openai_key,
        openai_model=data.openai_model,
        captcha_llm_provider=data.captcha_llm_provider,
        deepseek_api_key=deepseek_key,
        extra_toggles=data.extra_toggles,
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
            "text_llm_provider": secret.text_llm_provider,
            "has_deepseek_key": bool(secret.deepseek_api_key),
            "extras_enabled": sum(secret.extra_toggles.values()),
        },
    )
    return _mask_settings(secret)


async def lift_dm_pause() -> WarmingSettings:
    """«Снять паузу»: reopen fleet DMs now, spending the blocks that tripped the pause.

    A no-op without a pause in force, so a stray call cannot wipe the running count.
    """
    now = datetime.now(UTC)
    if active_dm_pause((await load_warming_settings()).dm_paused_until, now) is not None:
        await store_dm_paused_until(now.isoformat())
        await log_event("INFO", "warming_dm_fleet_pause_lifted")
    return await load_settings()
