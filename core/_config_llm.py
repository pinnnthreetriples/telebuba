"""LLM provider settings — split from ``core._config_domains`` for size.

The three text/vision gateways the fleet talks to, cut out as one domain: they share
one wire family (``DeepseekSettings`` subclasses ``OpenAISettings``, and
``core.openai`` drives both), and nothing else in the settings tree reads them.
Re-exported via ``core._config_domains`` — and thus ``core.config`` — so
``from core.config import GeminiSettings`` keeps working unchanged.
"""

# aislop-ignore-file ai-slop/hardcoded-url -- every URL here is an env-overridable
# Settings default (validated HTTPS); that is what a config default IS, not a URL
# buried in business logic, which is what the rule is for.

from __future__ import annotations

from typing import ClassVar

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GeminiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEMINI__", extra="ignore")

    api_key: str = Field(default="", repr=False)
    model: str = Field(default="gemini-2.5-flash")
    base_url: str = Field(default="https://generativelanguage.googleapis.com/v1beta")
    timeout_seconds: float = Field(default=30.0, ge=1.0)
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)
    # Reply budget for the short-text callers (comments, warming chat), which run with
    # thinking off. 30 words of Cyrillic or Arabic tokenizes far heavier than of
    # English, so this sits well above the worst case: hitting the cap is now a
    # failed generation, and a retry cannot widen the budget.
    max_output_tokens: int = Field(default=256, ge=1, le=2048)
    # Retry a transient failure (429 / 5xx / transport error) this many times
    # before surfacing it; the shared client is reused across calls so a hot-path
    # generate_text does not pay a fresh TLS handshake each time.
    max_retries: int = Field(default=1, ge=0, le=5)
    # Backoff slept between retries (seconds); kept short so the warming loop is
    # not blocked long on a flapping upstream.
    retry_backoff_seconds: float = Field(default=1.0, ge=0.0)
    # Minimum spacing between Gemini calls (seconds); 0 = no throttle. The default
    # seeds the operator-editable settings-row column and is the gateway fallback
    # when a request does not carry its own override.
    min_interval_seconds: float = Field(default=0.0, ge=0.0)


class OpenAISettings(BaseSettings):
    """Alternative captcha-solver LLM (OpenAI/ChatGPT).

    A separate key from Gemini, used only for challenge solving when the operator
    selects the ``openai`` provider. GPT vision handles image captchas well, so
    this is the recommended provider for the hardest challenges. The key is
    operator-set in the DB (falls back to ``OPENAI__API_KEY`` in .env).
    """

    model_config = SettingsConfigDict(env_prefix="OPENAI__", extra="ignore")

    # Whether this provider takes the ``thinking`` request field. A capability of the
    # API, not a knob: an operator cannot make OpenAI accept it, and sending it there
    # is a rejected request. ClassVar so it is not a settings field and cannot be set
    # from the environment.
    sends_thinking: ClassVar[bool] = False

    api_key: str = Field(default="", repr=False)
    model: str = Field(default="gpt-4o")
    base_url: str = Field(default="https://api.openai.com/v1")
    timeout_seconds: float = Field(default=30.0, ge=1.0)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=300, ge=1, le=2048)
    max_retries: int = Field(default=1, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=1.0, ge=0.0)


class DeepseekSettings(OpenAISettings):
    """DeepSeek — the text generator, and the reason Gemini now only does vision.

    Subclasses the OpenAI settings because DeepSeek serves the same wire format
    (``POST {base_url}/chat/completions``, ``Bearer`` key), which is what lets
    ``core.openai`` drive both without a second gateway. Only the defaults differ.

    The key lives HERE and not in the operator's DB record, unlike every other
    provider: the Gemini/OpenAI keys are UI-set because the operator rotates them
    per campaign, and this one is deployment config. That is also the fallback
    switch — an empty key sends text generation back to Gemini rather than failing,
    so a deployment that has not set ``DEEPSEEK__API_KEY`` keeps working unchanged.

    ``deepseek-v4-flash`` is TEXT-ONLY (DeepSeek publishes ``input_modalities:
    ["text"]``), so nothing carrying an image may be routed here — ``_generate`` and
    ``services.warming._chat_text`` both keep the image path on Gemini.
    """

    model_config = SettingsConfigDict(env_prefix="DEEPSEEK__", extra="ignore")

    # V4 thinks by DEFAULT (``thinking.type`` defaults to "enabled" at "high" effort)
    # and charges the thoughts to ``max_tokens``, the same trap Gemini set: omit the
    # field and reasoning eats the whole budget, leaving a ``finish_reason: "length"``
    # the gateway turns into an error — every comment would simply fail. So it is sent.
    sends_thinking: ClassVar[bool] = True

    model: str = Field(default="deepseek-v4-flash")
    base_url: str = Field(default="https://api.deepseek.com")
    # Double the siblings': a live day lost whole posts to two 30s timeouts a round.
    timeout_seconds: float = Field(default=60.0, ge=1.0)
    # Generation defaults, not the solver's: this provider writes comments and
    # warming replies, so it inherits Gemini's shape rather than OpenAI's 0.0/300.
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=256, ge=1, le=2048)
