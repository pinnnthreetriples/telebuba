"""Scheduled profile-post settings — its own module for the file-size budget.

Re-exported through ``core.config`` like every sibling domain module.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScheduledPostsSettings(BaseSettings):
    """Tunables for timed profile-photo / story publishing."""

    model_config = SettingsConfigDict(env_prefix="SCHEDULED_POSTS__", extra="ignore")

    # Content-addressed store for uploaded media waiting for its publish time.
    # Relative paths resolve against the working directory, like ``sessions/``.
    media_dir: Path = Path("runtime/scheduled_media")
    # A post the server could not publish on time (it was down) still goes out
    # within this window after its planned time; later it becomes ``missed``.
    missed_grace_seconds: int = Field(default=21_600, ge=60)
    # How far ahead of now a publish time must be, and how far it may be.
    min_lead_seconds: int = Field(default=60, ge=0)
    max_lead_days: int = Field(default=365, ge=1)
    # Gap between ANY two scheduled publishes (fleet-wide), jittered per post, so
    # a backlog after a restart does not leave in one coordinated burst.
    global_gap_min_seconds: float = Field(default=20.0, ge=0.0)
    global_gap_max_seconds: float = Field(default=60.0, ge=0.0)
    # Floor between two scheduled publishes of the SAME account.
    account_gap_seconds: float = Field(default=60.0, ge=0.0)
    # Backoff for a publish that never reached Telegram (pool/connect failure).
    retry_base_seconds: int = Field(default=60, ge=1)
    retry_max_seconds: int = Field(default=1800, ge=1)
    # Settled rows (done, cancelled, and failed / missed / ambiguous ones nobody
    # moved) are dropped this many days after they last changed.
    retention_days: int = Field(default=14, ge=1)
    max_pending_per_account: int = Field(default=50, ge=1)
    max_store_bytes: int = Field(default=5_000_000_000, ge=1)
    # An uploaded file nothing references yet survives this long, so the gap
    # between "uploaded" and "scheduled" can never lose it.
    media_gc_grace_seconds: int = Field(default=3600, ge=60)
    # The worker re-checks the clock at least this often, whatever it waits for.
    max_sleep_seconds: float = Field(default=60.0, gt=0.0)
    # How long shutdown lets an in-flight publish finish before cancelling it.
    shutdown_drain_seconds: float = Field(default=20.0, gt=0.0)
    # Skip the avatar re-sync when another photo of the account is due this soon.
    avatar_resync_skip_seconds: int = Field(default=300, ge=0)
    thumb_max_px: int = Field(default=320, ge=32, le=1080)

    @model_validator(mode="after")
    def _ordered_gap(self) -> ScheduledPostsSettings:
        if self.global_gap_max_seconds < self.global_gap_min_seconds:
            msg = "SCHEDULED_POSTS__GLOBAL_GAP_MAX_SECONDS must be >= the minimum"
            raise ValueError(msg)
        return self
