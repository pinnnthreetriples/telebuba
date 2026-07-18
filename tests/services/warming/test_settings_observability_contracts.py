"""Settings persistence and audit-event contracts."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from schemas.warming import WarmingSettingsSecret, WarmingSettingsUpdate
from services.warming import settings_store


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("clear_keys", "expected_gemini_key", "expected_openai_key"),
    [
        (False, "gemini-new", "openai-new"),
        (True, "", ""),
    ],
)
async def test_save_settings_persists_controls_and_emits_masked_audit_payload(
    monkeypatch: pytest.MonkeyPatch,
    clear_keys: bool,  # noqa: FBT001 - table input models the two flag states.
    expected_gemini_key: str,
    expected_openai_key: str,
) -> None:
    stored = WarmingSettingsSecret(
        inter_account_chat=True,
        reactions_enabled=False,
        join_enabled=False,
        enforce_readiness=False,
        max_daily_actions=37,
        gemini_api_key=expected_gemini_key,
        gemini_model="gemini-contract",
        gemini_max_retries=4,
        gemini_min_interval_seconds=2.5,
        openai_api_key=expected_openai_key,
        openai_model="gpt-contract",
        captcha_llm_provider="openai",
        updated_at="2026-07-17T12:00:00+00:00",
    )
    persist = AsyncMock(return_value=stored)
    log = AsyncMock()
    monkeypatch.setattr(settings_store, "save_warming_settings", persist)
    monkeypatch.setattr(settings_store, "log_event", log)

    result = await settings_store.save_settings(
        WarmingSettingsUpdate(
            inter_account_chat=True,
            reactions_enabled=False,
            join_enabled=False,
            enforce_readiness=False,
            max_daily_actions=37,
            gemini_api_key="gemini-new",
            gemini_model="gemini-contract",
            gemini_max_retries=4,
            gemini_min_interval_seconds=2.5,
            clear_gemini_key=clear_keys,
            openai_api_key="openai-new",
            openai_model="gpt-contract",
            clear_openai_key=clear_keys,
            captcha_llm_provider="openai",
        )
    )

    persist.assert_awaited_once_with(
        inter_account_chat=True,
        reactions_enabled=False,
        join_enabled=False,
        enforce_readiness=False,
        max_daily_actions=37,
        gemini_api_key=expected_gemini_key,
        gemini_model="gemini-contract",
        gemini_max_retries=4,
        gemini_min_interval_seconds=2.5,
        openai_api_key=expected_openai_key,
        openai_model="gpt-contract",
        captcha_llm_provider="openai",
    )
    assert result.model_dump() == {
        "inter_account_chat": True,
        "reactions_enabled": False,
        "join_enabled": False,
        "enforce_readiness": False,
        "max_daily_actions": 37,
        "has_gemini_key": bool(expected_gemini_key),
        "gemini_model": "gemini-contract",
        "gemini_max_retries": 4,
        "gemini_min_interval_seconds": 2.5,
        "has_openai_key": bool(expected_openai_key),
        "openai_model": "gpt-contract",
        "captcha_llm_provider": "openai",
        "updated_at": "2026-07-17T12:00:00+00:00",
    }
    log.assert_awaited_once_with(
        "INFO",
        "warming_settings_saved",
        extra={
            "inter_account_chat": True,
            "reactions_enabled": False,
            "join_enabled": False,
            "enforce_readiness": False,
            "max_daily_actions": 37,
            "has_gemini_key": bool(expected_gemini_key),
            "gemini_model": "gemini-contract",
            "gemini_max_retries": 4,
            "gemini_min_interval_seconds": 2.5,
            "has_openai_key": bool(expected_openai_key),
            "captcha_llm_provider": "openai",
        },
    )


@pytest.mark.asyncio
async def test_save_settings_preserves_keys_when_update_omits_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = WarmingSettingsSecret(
        inter_account_chat=False,
        reactions_enabled=True,
        gemini_api_key="kept-gemini",
        gemini_model="gemini-default",
        openai_api_key="kept-openai",
        updated_at="2026-07-17T12:00:00+00:00",
    )
    persist = AsyncMock(return_value=stored)
    monkeypatch.setattr(settings_store, "save_warming_settings", persist)
    monkeypatch.setattr(settings_store, "log_event", AsyncMock())

    result = await settings_store.save_settings(WarmingSettingsUpdate())

    call = persist.await_args
    assert call is not None
    assert call.kwargs["gemini_api_key"] is None
    assert call.kwargs["openai_api_key"] is None
    assert result.has_gemini_key is True
    assert result.has_openai_key is True
