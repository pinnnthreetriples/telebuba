"""Survival counter settings: the @SpamBot checks on days 7 and 30 after release.

Re-exported through ``core.config`` like ``core._config_soft_start``. The windows
themselves (7 and 30 days) are the product's definition and live in
``schemas.survival``; this module holds only the pacing knobs.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SurvivalSettings(BaseSettings):
    """Pacing of the survival probe loop and the panel's read sizes."""

    model_config = SettingsConfigDict(env_prefix="SURVIVAL__", extra="ignore")

    # Ask @SpamBot on day 7 and day 30 after release (one answer per window).
    probes_enabled: bool = True
    # One pass this often; each pass asks about at most one account.
    probe_interval_seconds: float = Field(default=3600.0, gt=0.0)
    # The first pass waits this long after startup, so a restart does not probe at once.
    probe_startup_delay_seconds: float = Field(default=600.0, ge=0.0)
    # No two accounts behind the same proxy are asked within this many seconds.
    probe_proxy_spacing_seconds: float = Field(default=3600.0, ge=0.0)
    # An account whose probe got no answer is not asked again for this long.
    probe_retry_hours: float = Field(default=24.0, ge=0.0)
    # Days of fleet-wide daily history the panel charts.
    history_days: int = Field(default=30, ge=1, le=366)
    # Most stints the swimmer plot lists, newest start first.
    stint_rows: int = Field(default=200, ge=1)
