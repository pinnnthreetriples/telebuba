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
    # How long a web window's launch waits for the pre-login listing of authorizations; on
    # expiry the login goes on and the recording falls back to the time window above.
    own_login_baseline_timeout_seconds: float = Field(default=10.0, gt=0.0)
    # A re-keyed account's old authorization that could not be ended yet is tried again after
    # this long (or after the wait Telegram named).
    rekey_retry_seconds: float = Field(default=3600.0, gt=0.0)
    # Whether Telebuba ends a re-keyed account's old authorization on its own, right after the
    # re-key and then from the session guard. Off, the old authorization stays until the
    # operator closes it from the card, and the guard only reminds: a young unofficial session
    # ending an older official one is what an account takeover looks like to Telegram.
    auto_close_old_key: bool = False
    # The card's checklist before the operator closes an old authorization by hand: the new
    # session is at least this old, and SpamBot said nothing but "clean" for this long.
    old_key_min_age_days: float = Field(default=7.0, gt=0.0)
    old_key_spam_clean_days: float = Field(default=30.0, gt=0.0)
    # The guard reminds about an old authorization still open at most this often per account.
    old_key_reminder_hours: float = Field(default=24.0, gt=0.0)
    # One account's old-key close (read, close, read again) may take this long in the
    # background pass before it is given up for now and tried again later, so one stuck
    # account cannot hold every other one behind it.
    old_key_account_timeout_seconds: float = Field(default=300.0, gt=0.0)
    # An action or audit that finds an account's key dead starts a session check for it, at
    # most once per this many seconds per account.
    death_check_cooldown_seconds: float = Field(default=600.0, gt=0.0)
    # The session guard's background task makes a pass this often: it ends old keys that are
    # due and re-audits at most ONE account (below).
    guard_interval_seconds: float = Field(default=300.0, gt=0.0)
    # One guard step (ending due old keys, or the watch's one read) may run this long before
    # it is abandoned and logged, so a hung step cannot freeze the loop for every account.
    # The worst legitimate step is the old-key one: each account is a Telegram call bounded by
    # ``telegram.session_check_timeout_seconds`` (75 s) plus the human-like pauses between
    # closes, for however many accounts are due at once; 30 minutes is ~24 such reads.
    guard_step_timeout_seconds: float = Field(default=1800.0, gt=0.0)
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
