"""Session audit settings: close pacing, the re-key's old-key retries and the background watch.

Re-exported through ``core.config`` like ``core._config_survival``. Ending several sessions
of one account in a burst is what an automated client does, so the service draws a
human-like pause for every gap from these bounds (core holds no randomness).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SessionAuditSettings(BaseSettings):
    """Pacing of ``account.resetAuthorization`` calls against one account."""

    model_config = SettingsConfigDict(env_prefix="SESSION_AUDIT__", extra="ignore")

    # The gap between two sessions of one account being ended is drawn from this range.
    close_pause_min_seconds: float = Field(default=20.0, ge=0.0)
    close_pause_max_seconds: float = Field(default=60.0, ge=0.0)
    # Shape of the clipped log-normal the pause is drawn from (chat broadcast's values).
    delay_lognorm_mu: float = -0.8
    delay_lognorm_sigma: float = Field(default=0.6, gt=0.0)
    # Only when the account's authorizations could not be listed before its web window was
    # launched: a Web authorization counts as made by Telebuba's own login when it was
    # created within [the drive's start - skew, the moment logged-in was seen + skew]
    # (Telegram's clock against ours).
    own_login_skew_seconds: float = Field(default=30.0, ge=0.0)
    # How long a web window's launch waits for the pre-login listing of authorizations; on
    # expiry the login goes on and the recording falls back to the time window above.
    own_login_baseline_timeout_seconds: float = Field(default=10.0, gt=0.0)
    # A re-keyed account's old authorization that could not be ended yet is tried again after
    # this long (or after the wait Telegram named).
    rekey_retry_seconds: float = Field(default=3600.0, gt=0.0)
    # The session guard's background task makes a pass this often: it ends old keys that are
    # due and re-audits at most ONE account (below).
    guard_interval_seconds: float = Field(default=300.0, gt=0.0)
    # The watch re-reads every alive account's sessions about this often; each account's own
    # interval is drawn afresh from the range (a clipped log-normal, in the service), so the
    # fleet never reads in step.
    watch_enabled: bool = True
    watch_min_hours: float = Field(default=6.0, gt=0.0)
    watch_max_hours: float = Field(default=12.0, gt=0.0)
    # The first pass waits this long after startup, so a restart does not read at once.
    watch_startup_delay_seconds: float = Field(default=900.0, ge=0.0)
    # No two accounts behind the same proxy are read within this many seconds.
    watch_proxy_spacing_seconds: float = Field(default=1800.0, ge=0.0)
    # An account whose read was refused is tried again after this long.
    watch_retry_hours: float = Field(default=1.0, gt=0.0)
