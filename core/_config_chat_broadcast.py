"""Chat-broadcast engine settings — its own module for the file-size budget.

Re-exported through ``core.config`` like ``core._config_neuroshilling``. Everything the
operator tunes per campaign (pauses, limits, rounds) lives in the campaign's own
settings; this module holds the fleet-wide ceilings and the engine's own timings.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ``schemas.gemini.GeminiRequest.max_output_tokens`` is bounded ``le=2048``.
_GEMINI_OUTPUT_CEILING = 2048


class ChatBroadcastSettings(BaseSettings):
    """Tunables for the chat-broadcast engine — no magic numbers in the code."""

    model_config = SettingsConfigDict(env_prefix="CHAT_BROADCAST__", extra="ignore")

    # Photos attached to chain messages, content-addressed; size and type limits are the
    # profile-photo ones (``settings.profile_media``).
    media_dir: Path = Path("runtime/chat_broadcast_media")
    max_targets_per_campaign: int = Field(default=500, ge=1)
    max_target_length: int = Field(default=200, ge=1)
    max_accounts_per_campaign: int = Field(default=100, ge=1)
    # Dialogs scanned per account for "where it already is"; a bound, not a target.
    dialogs_scan_limit: int = Field(default=500, ge=1, le=2000)
    # Floor between two sends by the SAME account across every chat it serves, enforced by
    # ``services.pacing``. Small on purpose: the operator's own 3-8 s between the messages
    # of one chain must still be possible, the per-campaign pauses do the real spacing.
    send_min_gap_seconds: float = Field(default=2.0, ge=0.0)
    # Spacing between joins of one account, jittered per join — the numbers neurocomment
    # and neuroshilling use, because the limit they respect belongs to Telegram.
    join_gap_seconds: float = Field(default=30.0, ge=0.0)
    # Rolling-24h join ceiling per account, counted out of the shared join log every
    # feature writes (0 = no cap): Telegram counts joins per ACCOUNT.
    max_joins_per_account_per_day: int = Field(default=20, ge=0)
    # How often a pending join request is re-checked; the operator sets how long to wait.
    approval_poll_seconds: float = Field(default=900.0, gt=0.0)
    # Longest a worker sleeps without re-reading its queue, so a manual "write now" or
    # "hand over" from the board is picked up without a restart.
    idle_poll_seconds: float = Field(default=30.0, gt=0.0)
    # Longest a rest between rounds sleeps without re-reading its end, so a rest the
    # board's gear shortened is over within this much of its new end.
    rest_poll_seconds: float = Field(default=30.0, gt=0.0)
    # Back-off after the account's connection dropped mid-chat.
    reconnect_delay_seconds: float = Field(default=60.0, gt=0.0)
    # Back-off after the account hit its own join cap or hourly/daily message limit.
    limit_retry_seconds: float = Field(default=900.0, gt=0.0)
    # "Typing…" lasts proportionally to the text, within these bounds.
    typing_chars_per_second: float = Field(default=12.0, gt=0.0)
    typing_min_seconds: float = Field(default=1.5, ge=0.0)
    typing_max_seconds: float = Field(default=8.0, ge=0.0, le=10.0)
    stop_drain_seconds: float = Field(default=20.0, gt=0.0)
    delay_lognorm_mu: float = -0.8
    delay_lognorm_sigma: float = Field(default=0.6, gt=0.0)
    # Rolling-24h ceiling on rewrite / "AI for the chat" calls across the fleet. Past it
    # the operator's own text goes out, so the cap trades variety, never delivery.
    max_llm_calls_per_day: int = Field(default=500, ge=0)
    llm_max_output_tokens: int = Field(default=1024, ge=1, le=_GEMINI_OUTPUT_CEILING)
    # A rewrite longer than the original by this factor is a model that wandered off.
    rewrite_max_growth: float = Field(default=2.0, ge=1.0)
    # Board history per chat: the newest entries only.
    history_per_chat: int = Field(default=30, ge=1)

    @model_validator(mode="after")
    def _check_typing_bounds(self) -> ChatBroadcastSettings:
        if self.typing_min_seconds > self.typing_max_seconds:
            msg = "typing_min_seconds must not exceed typing_max_seconds"
            raise ValueError(msg)
        return self
