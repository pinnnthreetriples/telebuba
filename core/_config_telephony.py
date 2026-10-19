"""Inbound telephony — the SMS gateway webhook and the phone's OTP wait.

Re-exported through ``core.config`` like ``core._config_soft_start``. The webhook is
answered only when ``webhook_secret`` is set: a gateway sends it in the
``X-Telephony-Token`` header, since GoIP/Dinstar boxes cannot hold an operator
session. Everything the dispatcher keeps lives in this one process (sessions, the
duplicate window, the inbox), which the project's one-uvicorn-worker rule allows.
"""

from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MIN_WEBHOOK_SECRET_BYTES = 32


class TelephonySettings(BaseSettings):
    """The webhook's shared secret, its replay window, and how long a phone waits."""

    model_config = SettingsConfigDict(env_prefix="TELEPHONY__", extra="ignore")

    # Empty turns the webhook off (503); when set, 32+ bytes like AUTH__SECRET.
    webhook_secret: str = Field(default="", repr=False)
    # A gateway's own ``timestamp`` may differ from ours by at most this many seconds.
    max_clock_skew_seconds: float = Field(default=300.0, gt=0)
    # The same SMS (number, sender, text) is answered once within this window; it must
    # cover twice the skew so a replay inside the skew can never be typed again.
    dedup_ttl_seconds: float = Field(default=600.0, gt=0)
    # Prefix for a national number a gateway sends without one (``9991234567``).
    default_country_code: str = Field(default="7", pattern=r"^[0-9]{1,3}$")
    # How long a phone waits for its SMS, and how long an ended wait stays visible.
    session_ttl_seconds: int = Field(default=180, ge=10)
    session_retention_seconds: int = Field(default=3600, ge=60)
    # The reading pause before the code is typed, seconds.
    reading_pause_min_seconds: float = Field(default=5.0, ge=0)
    reading_pause_max_seconds: float = Field(default=7.5, ge=0)

    @model_validator(mode="after")
    def _check_values(self) -> TelephonySettings:
        """Refuse a short secret, a replay window shorter than the skew, an empty pause range."""
        if self.webhook_secret and len(self.webhook_secret.encode()) < _MIN_WEBHOOK_SECRET_BYTES:
            msg = "TELEPHONY__WEBHOOK_SECRET must be at least 32 bytes when set"
            raise ValueError(msg)
        if self.dedup_ttl_seconds < 2 * self.max_clock_skew_seconds:
            msg = (
                "TELEPHONY__DEDUP_TTL_SECONDS must be at least twice "
                "TELEPHONY__MAX_CLOCK_SKEW_SECONDS"
            )
            raise ValueError(msg)
        if self.reading_pause_min_seconds > self.reading_pause_max_seconds:
            msg = "TELEPHONY__READING_PAUSE_MIN_SECONDS must not exceed the max"
            raise ValueError(msg)
        return self
