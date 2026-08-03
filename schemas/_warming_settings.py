"""Warming *settings* schemas — split from ``schemas.warming`` for the file-size budget.

Data contract only, no behaviour (non-negotiable #2). Re-exported from
``schemas.warming`` so ``from schemas.warming import WarmingSettings`` etc. keep
working unchanged. Self-contained: depends only on pydantic + stdlib typing, so
``schemas.warming`` imports these back without a cycle.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Which LLM the captcha solver uses. Operator-chosen, stored on the settings row.
CaptchaLlmProvider = Literal["gemini", "openai"]


class WarmingSettings(BaseModel):
    """Masked, UI-facing warming settings — never carries the raw Gemini key."""

    inter_account_chat: bool = False
    reactions_enabled: bool = True
    join_enabled: bool = True
    enforce_readiness: bool = True
    has_gemini_key: bool = False
    gemini_model: str = Field(min_length=1)
    # Operator-tunable Gemini rate-limit handling (not secret): retry count on a
    # 429/5xx and the minimum spacing between calls (seconds; 0 = no throttle).
    gemini_max_retries: int = Field(default=1, ge=0, le=5)
    gemini_min_interval_seconds: float = Field(default=0.0, ge=0.0, le=60.0)
    # Captcha LLM: presence flag + model + provider choice (keys never surfaced).
    has_openai_key: bool = False
    openai_model: str = Field(default="gpt-4o", min_length=1)
    captcha_llm_provider: CaptchaLlmProvider = "gemini"
    # Telemetr.io channel-catalogue key (neurocomment channel discovery). Lives on
    # this row because it is the app's single settings row for provider keys.
    has_telemetr_key: bool = False
    updated_at: str = Field(min_length=1)


class WarmingSettingsSecret(BaseModel):
    """Internal read model — carries the raw LLM keys for ``core`` gateways only."""

    inter_account_chat: bool
    reactions_enabled: bool
    join_enabled: bool = True
    enforce_readiness: bool = True
    gemini_api_key: str
    gemini_model: str = Field(min_length=1)
    gemini_max_retries: int = Field(default=1, ge=0, le=5)
    gemini_min_interval_seconds: float = Field(default=0.0, ge=0.0, le=60.0)
    openai_api_key: str = ""
    openai_model: str = Field(default="gpt-4o", min_length=1)
    captcha_llm_provider: CaptchaLlmProvider = "gemini"
    telemetr_api_key: str = ""
    updated_at: str = Field(min_length=1)


class WarmingSettingsUpdate(BaseModel):
    """Caller-supplied settings change from the UI.

    ``gemini_api_key`` semantics: ``None`` leaves the stored key untouched, an
    empty string clears it, any other value replaces it. Same applies to
    ``gemini_model`` — ``None`` keeps current value, non-empty overrides.
    An explicit ``clear_gemini_key`` flag is provided so the UI can clear the
    stored key without ambiguity.
    """

    model_config = ConfigDict(extra="forbid")

    # Keep-semantics for the four toggles too (``None`` keeps the stored value). With a
    # hard default each, a caller sending only some of them — a PUT is a full
    # replacement, and the write path applies every field it is handed — silently reset
    # the toggles it never mentioned back to False/True/True/True.
    inter_account_chat: bool | None = None
    reactions_enabled: bool | None = None
    join_enabled: bool | None = None
    enforce_readiness: bool | None = None
    gemini_api_key: str | None = None
    gemini_model: str | None = None
    # Keep-semantics as well (``None`` keeps the stored value): a PUT is a full
    # replacement, so a caller sending only some of the fields — the warming board's
    # config modal does — reset these to the old defaults 1 and 0.0.
    gemini_max_retries: int | None = Field(default=None, ge=0, le=5)
    gemini_min_interval_seconds: float | None = Field(default=None, ge=0.0, le=60.0)
    clear_gemini_key: bool = False
    # Same keep/clear/replace semantics for the captcha OpenAI key + model, plus
    # the provider selector (None keeps the stored value).
    openai_api_key: str | None = None
    openai_model: str | None = None
    clear_openai_key: bool = False
    captcha_llm_provider: CaptchaLlmProvider | None = None
    # Same keep/clear/replace semantics for the Telemetr.io discovery key.
    telemetr_api_key: str | None = None
    clear_telemetr_key: bool = False
