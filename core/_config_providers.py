"""Compatibility exports for provider settings split into canonical domain modules."""

from __future__ import annotations

from core._config_llm import DeepseekSettings, GeminiSettings, OpenAISettings

__all__ = [
    "DeepseekSettings",
    "GeminiSettings",
    "OpenAISettings",
]
