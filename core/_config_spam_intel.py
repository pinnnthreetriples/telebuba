"""Spam-intel settings: the one budget every @SpamBot question shares (docs/spam-intel.md).

Re-exported through ``core.config`` like ``core._config_survival``. Every value is
validated here so a typo in ``.env`` fails at startup instead of silently widening the
budget. A change takes effect after a restart; nothing needs a deploy.
"""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SpamIntelSettings(BaseSettings):
    """Gateway switches, the probe budget and the reply-sample caps."""

    model_config = SettingsConfigDict(env_prefix="SPAM_INTEL__", extra="ignore")

    # Master switch. Off: every caller reaches @SpamBot exactly as before the gateway
    # (the legacy cache/force semantics, no ledger, no budget).
    enabled: bool = True
    # Strict parser: a reply no marker recognises is ``unknown``, never ``clean``. Off
    # until reply samples exist for the languages the fleet speaks (docs/spam-intel.md,
    # risk R3); while off, ``parser_shadow`` logs where the two would differ.
    strict_parser: bool = False
    parser_shadow: bool = True
    # Keep redacted, deduplicated copies of the bot's replies for building fixtures.
    sample_capture: bool = True
    # One-off, read-only pass at startup: read each account's existing @SpamBot dialog
    # (no ``/start``) to collect real reply texts. Off: it never runs. It is a Telegram
    # request on every eligible account, so it needs the owner's explicit go.
    history_sweep: bool = False
    history_messages: int = Field(default=20, ge=1, le=100)
    # The sweep waits this long after startup, then reads one account at a time.
    history_startup_delay_minutes: float = Field(default=15.0, ge=0.0)

    # A real reading, or a failed attempt, younger than this is handed to every caller
    # that asks again, forced or not.
    coalesce_minutes: float = Field(default=30.0, gt=0.0)
    # No two real probes behind the same proxy within this long.
    proxy_spacing_minutes: float = Field(default=120.0, ge=0.0)
    # Between any two real probes anywhere, a gap drawn from this range (seconds).
    fleet_gap_min_seconds: float = Field(default=180.0, ge=0.0)
    fleet_gap_max_seconds: float = Field(default=600.0, ge=0.0)
    # Rolling 24 h: past the soft cap routine and measurement probes wait (a decision or
    # an operator click goes on, with a warning); past the hard cap nothing goes.
    fleet_soft_cap: int = Field(default=10, ge=1)
    fleet_hard_cap: int = Field(default=24, ge=1)
    # Rolling 24 h per account, for every purpose but the operator's.
    account_daily_cap: int = Field(default=4, ge=1)
    # The Spam-check button: extra probes per account per rolling 24 h, and how recent a
    # probe must be for the click to need the operator's confirmation.
    operator_daily_cap: int = Field(default=3, ge=1)
    out_of_turn_hours: float = Field(default=24.0, ge=0.0)
    # After an unreadable answer or a failure: this long, doubling per consecutive
    # failure up to the cap (6, 12, 24, 48 h by default).
    backoff_base_hours: float = Field(default=6.0, gt=0.0)
    backoff_max_hours: float = Field(default=48.0, gt=0.0)
    # Routine probes wait this long after the process starts (a restart makes every
    # loop due at once).
    startup_quiet_minutes: float = Field(default=10.0, ge=0.0)
    # A busy account (another feature runs on it) is asked again after this long.
    busy_retry_minutes: float = Field(default=10.0, gt=0.0)

    # Ledger and sample bounds.
    attempts_retention_days: int = Field(default=90, ge=1)
    attempts_max_rows: int = Field(default=5000, ge=100)
    samples_max_rows: int = Field(default=200, ge=1)
    sample_max_chars: int = Field(default=1000, ge=100)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.fleet_gap_min_seconds > self.fleet_gap_max_seconds:
            msg = "fleet_gap_min_seconds must not exceed fleet_gap_max_seconds"
            raise ValueError(msg)
        if self.fleet_soft_cap > self.fleet_hard_cap:
            msg = "fleet_soft_cap must not exceed fleet_hard_cap"
            raise ValueError(msg)
        if self.backoff_base_hours > self.backoff_max_hours:
            msg = "backoff_base_hours must not exceed backoff_max_hours"
            raise ValueError(msg)
        return self
