"""Settings of the idle-account @SpamBot re-check pass (docs/spam-intel.md, D5).

Own small module so the gateway's budget settings (``core._config_spam_intel``) stay where they
are. The pass asks the gateway as an ordinary ROUTINE ask; this only says how often it looks and
how long an account the gateway turned away is left alone. A change takes effect after a restart.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The largest interval in minutes (about 1.9 years): anything beyond it is no schedule.
_MAX_MINUTES = 1_000_000.0


class SpamRecheckSettings(BaseSettings):
    """The scheduler pass that re-asks accounts whose stored verdict is due a re-check."""

    model_config = SettingsConfigDict(
        env_prefix="SPAM_RECHECK__", extra="ignore", allow_inf_nan=False
    )

    # Off: the pass never runs. It also needs ``SPAM_INTEL__ENABLED`` and, because acting on the
    # evidence is a decision, ``SPAM_INTEL__PASSIVE_DECISIONS``.
    enabled: bool = True
    # One account per pass, one pass per this many minutes: the cadence, never a burst.
    scan_minutes: float = Field(default=30.0, ge=1.0, le=_MAX_MINUTES)
    # An account the gateway turned away (later, or no) is not picked again for this long unless
    # the answer carried its own retry time.
    hold_minutes: float = Field(default=120.0, ge=1.0, le=_MAX_MINUTES)
