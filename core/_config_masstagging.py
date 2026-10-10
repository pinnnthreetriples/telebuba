"""Masstagging engine settings — its own module for the file-size budget.

Re-exported through ``core.config`` like ``core._config_chat_broadcast``. What the
operator tunes per campaign (stories a day, pauses, the soft start) lives in the
campaign's settings; this module holds Telegram's own ceilings and the engine's timings.

``free_stories_per_day`` is the one number nobody could confirm (sources say 1, 3 and
"fewer"): it is a setting so the operator's test on a free account changes it without
a release.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MasstaggingSettings(BaseSettings):
    """Tunables for the masstagging engine — no magic numbers in the code."""

    model_config = SettingsConfigDict(env_prefix="MASSTAGGING__", extra="ignore")

    media_dir: Path = Path("runtime/masstagging_media")
    max_targets_per_campaign: int = Field(default=5000, ge=1)
    max_accounts_per_campaign: int = Field(default=100, ge=1)
    max_media_per_campaign: int = Field(default=10, ge=1)
    # Telegram's ceilings for an account without Premium, and with it.
    free_stories_per_day: int = Field(default=1, ge=0)
    premium_stories_per_day: int = Field(default=100, ge=1)
    free_mentions_per_story: int = Field(default=5, ge=1)
    free_caption_chars: int = Field(default=200, ge=1)
    premium_caption_chars: int = Field(default=2048, ge=1)
    # Username lookups one account may make a day.
    resolves_per_day: int = Field(default=200, ge=1)
    # The soft start: the account's first days in masstagging, fewer people, longer pauses.
    ramp_days: int = Field(default=3, ge=0)
    ramp_daily_cap: int = Field(default=6, ge=1)
    ramp_pause_min_seconds: int = Field(default=200, ge=0)
    ramp_pause_max_seconds: int = Field(default=600, ge=0)
    # How long the auto-delete timer stays on before it is switched off.
    ttl_hold_min_seconds: float = Field(default=1.0, ge=0.0, le=10.0)
    ttl_hold_max_seconds: float = Field(default=3.0, ge=0.0, le=10.0)
    # A chat link in the list stands for this many of its members.
    chat_members_limit: int = Field(default=200, ge=0, le=200)
    # Longest a worker sleeps without re-reading its queue, so "tag now" from the board
    # is picked up without a restart; and the slice a rest until tomorrow sleeps in.
    idle_poll_seconds: float = Field(default=30.0, gt=0.0)
    rest_poll_seconds: float = Field(default=60.0, gt=0.0)
    # Back-off after the account's connection dropped.
    reconnect_delay_seconds: float = Field(default=60.0, gt=0.0)
    stop_drain_seconds: float = Field(default=20.0, gt=0.0)
    # Board history per target: the newest entries only.
    history_per_target: int = Field(default=20, ge=1)
    # A target list file is plain text or CSV, one per line.
    import_max_bytes: int = Field(default=2_000_000, ge=1)

    @model_validator(mode="after")
    def _check_bounds(self) -> MasstaggingSettings:
        if self.ramp_pause_min_seconds > self.ramp_pause_max_seconds:
            msg = "ramp_pause_min_seconds must not exceed ramp_pause_max_seconds"
            raise ValueError(msg)
        if self.ttl_hold_min_seconds > self.ttl_hold_max_seconds:
            msg = "ttl_hold_min_seconds must not exceed ttl_hold_max_seconds"
            raise ValueError(msg)
        return self
