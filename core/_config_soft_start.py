"""Soft neurocomment start — lowered caps for an account's first days after hand-off.

Re-exported through ``core.config`` like ``core._config_notifications``. The operator
picks «Мягкий старт» at the hand-off; ``services._soft_start`` turns the hand-off time
into a day of the soft period and these numbers into that day's caps, which
``services._account_limits`` lowers the fleet caps to.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SoftStartSettings(BaseSettings):
    """The soft period and its two steps of caps; per-day comments ramp across it."""

    model_config = SettingsConfigDict(env_prefix="SOFT_START__", extra="ignore")

    days: int = Field(default=5, ge=1)
    # Days 1..early_days use the early caps, the rest of the period the late ones.
    early_days: int = Field(default=2, ge=0)
    early_comments_per_hour: int = Field(default=2, ge=1)
    early_comments_per_channel_per_day: int = Field(default=1, ge=1)
    early_joins_per_day: int = Field(default=5, ge=1)
    late_comments_per_hour: int = Field(default=4, ge=1)
    late_comments_per_channel_per_day: int = Field(default=2, ge=1)
    late_joins_per_day: int = Field(default=10, ge=1)
    # Rolling-24h comment cap per account, rising evenly from the first to the last day.
    first_day_comments: int = Field(default=5, ge=1)
    last_day_comments: int = Field(default=15, ge=1)
