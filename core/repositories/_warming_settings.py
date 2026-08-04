"""Warming *settings* persistence — split from ``core.repositories.warming``.

Owns the singleton ``warming_settings`` row: the secret read model, the
keep/clear/replace save semantics for the LLM keys/models + captcha provider,
and default-row seeding. Split out for the file-size budget; the public async
functions are re-exported by ``core.repositories.warming`` (and thence by
``core.db``) so existing call sites are unaffected.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from sqlalchemy import insert, select, update

from core.config import settings
from core.db import _get_engine, _now_iso, _warming_settings
from schemas.warming import CaptchaLlmProvider, WarmingSettingsSecret

if TYPE_CHECKING:
    from collections.abc import Mapping

_WARMING_SETTINGS_ID = 1


def _bool_or(value: object, default: bool) -> bool:  # noqa: FBT001
    return default if value is None else bool(value)


def _int_or(value: object, default: int) -> int:
    return default if value is None else int(cast("int | str", value))


def _float_or(value: object, default: float) -> float:
    return default if value is None else float(cast("float | int | str", value))


def _str_or(value: object, default: str) -> str:
    """DB column value if present + non-empty, else the config/.env fallback."""
    text = "" if value is None else str(value)
    return text or default


def _captcha_provider(value: object) -> CaptchaLlmProvider:
    text = "" if value is None else str(value)
    if text in ("gemini", "openai"):
        return cast("CaptchaLlmProvider", text)
    return settings.neurocomment.captcha_llm_provider


def _keep(new: str | None, current: object) -> str:
    """``None`` keeps the stored value; any value (incl. "" to clear) replaces it."""
    if new is None:
        return "" if current is None else str(current)
    return new


def _keep_nonempty(new: str | None, current: object, default: str) -> str:
    """``None``/"" keeps the stored value (else the default); a value replaces it."""
    if new:
        return new
    return str(current) if current else default


def _row_to_warming_settings_secret(mapping: Mapping[str, object]) -> WarmingSettingsSecret:
    # Columns added after the row was first created are nullable; a NULL means
    # "never set", so fall back to the config default to preserve old behaviour.
    # LLM keys/models + the captcha provider are operator-set in the UI and
    # persisted here; a blank/absent column falls back to the config/.env value
    # (so an env-only setup, and a rotated env value, both still work).
    warm = settings.warming
    return WarmingSettingsSecret(
        inter_account_chat=bool(mapping["inter_account_chat"]),
        reactions_enabled=bool(mapping["reactions_enabled"]),
        join_enabled=_bool_or(mapping.get("join_enabled"), default=True),
        enforce_readiness=_bool_or(mapping.get("enforce_readiness"), warm.enforce_readiness),
        gemini_api_key=_str_or(mapping.get("gemini_api_key"), settings.gemini.api_key),
        gemini_model=_str_or(mapping.get("gemini_model"), settings.gemini.model),
        gemini_max_retries=_int_or(mapping.get("gemini_max_retries"), settings.gemini.max_retries),
        gemini_min_interval_seconds=_float_or(
            mapping.get("gemini_min_interval_seconds"), settings.gemini.min_interval_seconds
        ),
        openai_api_key=_str_or(mapping.get("openai_api_key"), settings.openai.api_key),
        openai_model=_str_or(mapping.get("openai_model"), settings.openai.model),
        captcha_llm_provider=_captcha_provider(mapping.get("captcha_llm_provider")),
        telemetr_api_key=_str_or(mapping.get("telemetr_api_key"), settings.telemetr.api_key),
        updated_at=str(mapping["updated_at"]),
    )


def _default_warming_settings_values() -> dict[str, object]:
    warm = settings.warming
    return {
        "id": _WARMING_SETTINGS_ID,
        "inter_account_chat": 0,
        "reactions_enabled": 1,
        "join_enabled": 1,
        "enforce_readiness": int(warm.enforce_readiness),
        "gemini_api_key": "",
        "gemini_model": settings.gemini.model,
        "gemini_max_retries": settings.gemini.max_retries,
        "gemini_min_interval_seconds": settings.gemini.min_interval_seconds,
        "openai_api_key": "",
        "openai_model": settings.openai.model,
        "captcha_llm_provider": settings.neurocomment.captcha_llm_provider,
        "telemetr_api_key": "",
        "updated_at": _now_iso(),
    }


# Cache the singleton settings row so the per-challenge LLM path (challenge.py) does
# not re-open a SQLite transaction on every decision. Populated on read, dropped by
# _invalidate_warming_settings_cache() on any write or DB reconfigure.
_cached_settings: WarmingSettingsSecret | None = None


def _invalidate_warming_settings_cache() -> None:
    """Drop the cached settings row; call after any write or a DB reconfigure."""
    global _cached_settings  # noqa: PLW0603 - module-level singleton cache
    _cached_settings = None


def _load_warming_settings() -> WarmingSettingsSecret:
    global _cached_settings  # noqa: PLW0603 - module-level singleton cache
    if _cached_settings is not None:
        return _cached_settings
    statement = select(_warming_settings).where(_warming_settings.c.id == _WARMING_SETTINGS_ID)
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        # Seeding the defaults row is the only path that needs a write transaction.
        values = _default_warming_settings_values()
        with _get_engine().begin() as connection:
            connection.execute(insert(_warming_settings).values(**values))
        _cached_settings = _row_to_warming_settings_secret(cast("Mapping[str, object]", values))
        return _cached_settings
    _cached_settings = _row_to_warming_settings_secret(cast("Mapping[str, object]", row))
    return _cached_settings


async def load_warming_settings() -> WarmingSettingsSecret:
    """Return the singleton warming settings row, creating defaults on first read."""
    return await asyncio.to_thread(_load_warming_settings)


def _save_warming_settings(  # noqa: PLR0913 - one explicit column per setting reads clearer.
    *,
    inter_account_chat: bool | None = None,
    reactions_enabled: bool | None = None,
    join_enabled: bool | None = None,
    enforce_readiness: bool | None = None,
    gemini_api_key: str | None,
    gemini_model: str | None = None,
    gemini_max_retries: int | None = None,
    gemini_min_interval_seconds: float | None = None,
    openai_api_key: str | None = None,
    openai_model: str | None = None,
    captcha_llm_provider: str | None = None,
    telemetr_api_key: str | None = None,
) -> WarmingSettingsSecret:
    # Ensure the singleton row exists, then read it so a ``None`` key/model/provider
    # argument keeps the stored value (keep/clear/replace). Keys ARE persisted now
    # (operator-set in the UI); a blank column still falls back to .env on read.
    _load_warming_settings()
    with _get_engine().begin() as connection:
        current = (
            connection.execute(
                select(_warming_settings).where(_warming_settings.c.id == _WARMING_SETTINGS_ID),
            )
            .mappings()
            .first()
        )
        cur: Mapping[str, object] = dict(current) if current is not None else {}
        values: dict[str, object] = {
            # Keep-semantics like everything below: ``None`` keeps the stored value (else
            # the config default, for a row written before the column existed).
            "inter_account_chat": int(
                _bool_or(inter_account_chat, _bool_or(cur.get("inter_account_chat"), default=False))
            ),
            "reactions_enabled": int(
                _bool_or(reactions_enabled, _bool_or(cur.get("reactions_enabled"), default=True))
            ),
            "join_enabled": int(
                _bool_or(join_enabled, _bool_or(cur.get("join_enabled"), default=True))
            ),
            "enforce_readiness": int(
                _bool_or(
                    enforce_readiness,
                    _bool_or(cur.get("enforce_readiness"), settings.warming.enforce_readiness),
                )
            ),
            "gemini_api_key": _keep(gemini_api_key, cur.get("gemini_api_key")),
            "gemini_model": _keep_nonempty(
                gemini_model, cur.get("gemini_model"), settings.gemini.model
            ),
            # Keep-semantics like every neighbour above: ``None`` keeps the stored
            # value (else the config default). Written unconditionally, any caller
            # that omitted them — the warming board's config modal, a partial PUT —
            # silently reset the settings page's rate-limit knobs to 1 and 0.0.
            "gemini_max_retries": _int_or(
                gemini_max_retries,
                _int_or(cur.get("gemini_max_retries"), settings.gemini.max_retries),
            ),
            "gemini_min_interval_seconds": _float_or(
                gemini_min_interval_seconds,
                _float_or(
                    cur.get("gemini_min_interval_seconds"), settings.gemini.min_interval_seconds
                ),
            ),
            "openai_api_key": _keep(openai_api_key, cur.get("openai_api_key")),
            "openai_model": _keep_nonempty(
                openai_model, cur.get("openai_model"), settings.openai.model
            ),
            "captcha_llm_provider": _keep_nonempty(
                captcha_llm_provider,
                cur.get("captcha_llm_provider"),
                settings.neurocomment.captcha_llm_provider,
            ),
            "telemetr_api_key": _keep(telemetr_api_key, cur.get("telemetr_api_key")),
            "updated_at": _now_iso(),
        }
        connection.execute(
            update(_warming_settings)
            .where(_warming_settings.c.id == _WARMING_SETTINGS_ID)
            .values(**values),
        )
    _invalidate_warming_settings_cache()
    return _load_warming_settings()


async def save_warming_settings(  # noqa: PLR0913 - mirrors the explicit column list.
    *,
    inter_account_chat: bool | None = None,
    reactions_enabled: bool | None = None,
    join_enabled: bool | None = None,
    enforce_readiness: bool | None = None,
    gemini_api_key: str | None,
    gemini_model: str | None = None,
    gemini_max_retries: int | None = None,
    gemini_min_interval_seconds: float | None = None,
    openai_api_key: str | None = None,
    openai_model: str | None = None,
    captcha_llm_provider: str | None = None,
    telemetr_api_key: str | None = None,
) -> WarmingSettingsSecret:
    """Persist warming settings.

    LLM keys/models + the captcha provider use keep/clear/replace semantics:
    ``None`` keeps the stored value, ``""`` clears a key, any other value replaces.
    The two Gemini rate-limit knobs and the four toggles keep on ``None`` the same way.
    """
    return await asyncio.to_thread(
        _save_warming_settings,
        inter_account_chat=inter_account_chat,
        reactions_enabled=reactions_enabled,
        join_enabled=join_enabled,
        enforce_readiness=enforce_readiness,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
        gemini_max_retries=gemini_max_retries,
        gemini_min_interval_seconds=gemini_min_interval_seconds,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        captcha_llm_provider=captcha_llm_provider,
        telemetr_api_key=telemetr_api_key,
    )
