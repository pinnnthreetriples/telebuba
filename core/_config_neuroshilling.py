"""Neuroshilling engine settings — its own module for the file-size budget.

``core._config_domains`` is already close to the ceiling and this domain adds
twenty-odd keys, so it lives beside ``core._config_warming`` instead, for the
same reason that module gives. Re-exported through ``core.config`` so
``from core.config import NeuroshillingSettings`` works like every sibling.
"""

from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ``schemas.gemini.GeminiRequest.max_output_tokens`` is bounded ``le=2048``; a
# larger value here would only fail validation at the gateway.
_GEMINI_OUTPUT_CEILING = 2048


class NeuroshillingSettings(BaseSettings):
    """Tunables for the neuroshilling engine — no magic numbers in the code."""

    model_config = SettingsConfigDict(env_prefix="NEUROSHILLING__", extra="ignore")

    # A staged dialogue needs at least two voices to be a dialogue at all.
    min_accounts: int = Field(default=2, ge=1)
    max_targets_per_campaign: int = Field(default=20, ge=1)
    # Bound handed to ``core.channel_tokens.normalize_channel``; matches the
    # warming paste box, which is the other free-form target field in the app.
    max_target_length: int = Field(default=120, ge=1)
    max_roles: int = Field(default=5, ge=1)
    max_steps: int = Field(default=20, ge=1)
    # Spacing between channel joins, jittered per join. Same numbers neurocomment
    # uses, because the limit they respect belongs to Telegram, not to a feature.
    join_delay_min_seconds: float = Field(default=30.0, ge=0.0)
    join_delay_max_seconds: float = Field(default=120.0, ge=0.0)
    # Per-account rolling-24h join ceiling (0 = no cap). Telegram freezes an
    # account after roughly 20-50 joins in a day.
    max_joins_per_account_per_day: int = Field(default=20, ge=0)
    # Floor on the gap between two sends by the SAME account, enforced by
    # ``services.pacing``. Independent of the per-step delays, which are a
    # property of the dialogue rather than of the account.
    send_min_gap_seconds: float = Field(default=30.0, ge=0.0)
    # Same clipped log-normal shape warming draws its human pauses from.
    delay_lognorm_mu: float = -0.8
    delay_lognorm_sigma: float = Field(default=0.6, gt=0.0)
    # Rolling-24h ceiling on generation calls. Ten accounts across twenty targets
    # is four figures of logical calls before retries, and the project keeps no
    # token accounting at all, so the budget is counted in calls.
    max_llm_calls_per_day: int = Field(default=200, ge=0)
    # A DeepSeek JSON response is re-asked with the validator's complaint appended;
    # the provider also returns an empty body often enough to need a retry at all.
    llm_max_attempts: int = Field(default=3, ge=1, le=5)
    llm_max_output_tokens: int = Field(default=2048, ge=1, le=_GEMINI_OUTPUT_CEILING)
    # Wall clock on ONE generation, all attempts together. Their worst case is
    # attempts x (max_retries + 1) x the DeepSeek timeout — half an hour with both
    # budgets maxed — and the campaign answers 409 to every click for as long as it
    # runs, so the bound is on the clock rather than on the arithmetic.
    llm_deadline_seconds: float = Field(default=180.0, gt=0.0)
    # Per-MESSAGE ceiling on quoted chat context. Bounding the message COUNT alone
    # is not enough: Telegram allows 4096 characters each, so twenty of them would
    # crowd out the instruction by sheer volume.
    max_chat_context_chars: int = Field(default=500, ge=1)
    chat_context_messages: int = Field(default=20, ge=1)
    # Chat listening is polled, not subscribed — the listener belongs to
    # neurocomment and is not shared.
    poll_min_seconds: float = Field(default=30.0, ge=1.0)
    poll_max_seconds: float = Field(default=60.0, ge=1.0)
    # Probability of answering a given human message, per "reply activity" setting.
    reply_chance_calm: float = Field(default=0.1, ge=0.0, le=1.0)
    reply_chance_medium: float = Field(default=0.3, ge=0.0, le=1.0)
    reply_chance_active: float = Field(default=0.6, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_delay_bounds(self) -> NeuroshillingSettings:
        if self.join_delay_min_seconds > self.join_delay_max_seconds:
            msg = "join_delay_min_seconds must not exceed join_delay_max_seconds"
            raise ValueError(msg)
        if self.poll_min_seconds > self.poll_max_seconds:
            msg = "poll_min_seconds must not exceed poll_max_seconds"
            raise ValueError(msg)
        return self
