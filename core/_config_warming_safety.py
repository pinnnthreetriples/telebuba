"""Warming's spam-check, quarantine and content-safety tunables.

A mixin of :class:`core._config_warming.WarmingSettings`, split out for the file-size
gate: the fields are inherited, so their ``WARMING__*`` env names are unchanged.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WarmingSafetySettings(BaseModel):
    """@SpamBot cadence, PEER_FLOOD quarantine and outbound content filters."""

    # How long a cached @SpamBot verdict stays fresh before we re-probe. Frequent
    # /start to @SpamBot is itself suspicious, so keep this generous.
    spam_status_ttl_hours: float = Field(default=36.0, ge=0.0)
    # Scheduled @SpamBot check during warming: the first cycle of a stint asks, then
    # roughly every ``interval`` hours, shifted by a per-account offset within
    # ±``jitter`` so a fleet started together does not ask on the same day. After
    # ``warn_streak`` unreadable checks in a row the card warns the operator.
    spam_check_interval_hours: float = Field(default=72.0, gt=0.0)
    spam_check_jitter_hours: float = Field(default=12.0, ge=0.0)
    spam_unknown_warn_streak: int = Field(default=3, ge=1)
    # PEER_FLOOD quarantine: how long an account rests before its status is
    # re-checked, and how many consecutive still-limited re-checks are tolerated
    # before it is given up on (marked error + alerted).
    quarantine_hours: float = Field(default=48.0, gt=0.0)
    quarantine_max_repeats: int = Field(default=3, ge=1)
    # After a clean quarantine release: cap ramps from this share to full; DMs stay shut.
    release_ramp_start_fraction: float = Field(default=0.3, ge=0.0, le=1.0)
    release_ramp_days: float = Field(default=5.0, ge=0.0)
    release_dm_block_days: float = Field(default=14.0, ge=0.0)
    # Content anti-repeat: refuse to send the same normalised text twice within
    # this window (identical content across accounts is a strong spam signal),
    # how many times to regenerate before giving up, plus an outbound filter.
    content_dedup_window_days: float = Field(default=7.0, ge=0.0)
    content_max_attempts: int = Field(default=3, ge=1)
    content_block_links: bool = True
    content_forbidden_words: list[str] = Field(
        default_factory=lambda: ["реклама", "купить", "продам", "продаю", "скидк", "промокод"],
    )
