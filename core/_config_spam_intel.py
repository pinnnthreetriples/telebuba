"""Spam-intel settings: the one budget every @SpamBot question shares (docs/spam-intel.md).

Re-exported through ``core.config`` like ``core._config_survival``. Every value is
validated here so a typo in ``.env`` fails at startup instead of silently widening the
budget. A change takes effect after a restart; nothing needs a deploy.
"""

from __future__ import annotations

import math
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The largest interval, in the field's own unit (minutes, hours, seconds or days): about 114
# years of hours. Anything beyond it is no schedule, and ``timedelta``/``datetime`` arithmetic
# on it would overflow.
_MAX_INTERVAL = 1_000_000.0

# (min, max) field pairs of the Stage 3 ranges: each minimum must not exceed its maximum.
_RANGES = tuple(
    (f"{name}_min_seconds", f"{name}_max_seconds")
    for name in (
        "human_pre_open",
        "human_open_to_typing",
        "human_typing",
        "human_quiet",
        "human_read_delay",
        "live_session_delay",
    )
)


class SpamIntelSettings(BaseSettings):
    """Gateway switches, the probe budget and the reply-sample caps."""

    # ``allow_inf_nan=False``: an interval of ``inf`` or ``nan`` is a typo that would otherwise
    # surface as an ``OverflowError`` at the first ask, long after startup.
    model_config = SettingsConfigDict(
        env_prefix="SPAM_INTEL__", extra="ignore", allow_inf_nan=False
    )

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
    # The Accounts page warns when the operator's own checks (out of turn or not) over the last
    # 24 h reach this many across the fleet; the fleet's probes at the soft cap warn too.
    out_of_turn_warn_count: int = Field(default=5, ge=1)
    # After an unreadable answer or a failure: this long, doubling per consecutive
    # failure up to the cap (6, 12, 24, 48 h by default).
    backoff_base_hours: float = Field(default=6.0, gt=0.0)
    backoff_max_hours: float = Field(default=48.0, gt=0.0)
    # Routine probes wait this long after the process starts (a restart makes every
    # loop due at once).
    startup_quiet_minutes: float = Field(default=10.0, ge=0.0)
    # A busy account (another feature runs on it) is asked again after this long.
    busy_retry_minutes: float = Field(default=10.0, gt=0.0)
    # An account whose status is a transient fault (flood wait, proxy or network error: the session
    # check sets and clears them) is not asked now and is asked again after this long.
    account_status_retry_minutes: float = Field(default=30.0, gt=0.0)
    # D13: a ROUTINE probe waits out the account's local night (never a decision, a measurement or
    # the operator's click; an account whose timezone is unknown is never held). The night is the
    # local hours ``[start, end)``, wrapping midnight when start > end; start == end switches the
    # window off. The morning is staggered per account by up to ``spread`` minutes. On by default:
    # it only delays routine probes.
    local_night_defer: bool = True
    local_night_start_hour: int = Field(default=0, ge=0, le=23)
    local_night_end_hour: int = Field(default=7, ge=0, le=23)
    local_night_spread_minutes: float = Field(default=90.0, ge=0.0)

    # The probe as a person (Stage 3, design 5.7). ``human_sequence``: ask @SpamBot by opening
    # the dialog, pausing, "typing", pausing, sending ``/start``, collecting EVERY message of
    # the bot until it has been quiet for a while, pausing and marking the dialog read. Off: the
    # single ``/start`` probe as before. Each pause is drawn from its range (seconds) per probe.
    human_sequence: bool = False
    human_pre_open_min_seconds: float = Field(default=1.0, ge=0.0)
    human_pre_open_max_seconds: float = Field(default=4.0, ge=0.0)
    human_open_to_typing_min_seconds: float = Field(default=2.0, ge=0.0)
    human_open_to_typing_max_seconds: float = Field(default=6.0, ge=0.0)
    human_typing_min_seconds: float = Field(default=1.0, ge=0.0)
    human_typing_max_seconds: float = Field(default=3.0, ge=0.0)
    # How long to wait for the bot's FIRST message, and how long without a further one ends
    # the collection.
    human_reply_wait_seconds: float = Field(default=30.0, gt=0.0)
    human_quiet_min_seconds: float = Field(default=8.0, gt=0.0)
    human_quiet_max_seconds: float = Field(default=15.0, gt=0.0)
    human_read_delay_min_seconds: float = Field(default=1.0, ge=0.0)
    human_read_delay_max_seconds: float = Field(default=5.0, ge=0.0)
    # Inside a live session (Stage 3, design 5.7). On: a ROUTINE or DECISION ask for an account
    # whose pooled client is already connected is served a jittered moment into that session
    # (this range, seconds); a ROUTINE ask for an account with no live client is deferred (asked
    # again after ``live_session_retry_minutes``) instead of connecting it just to probe. A
    # DECISION, MEASUREMENT or operator ask still connects. Off: probes at the caller's moment.
    live_session_embedding: bool = False
    live_session_delay_min_seconds: float = Field(default=15.0, ge=0.0)
    live_session_delay_max_seconds: float = Field(default=120.0, ge=0.0)
    live_session_retry_minutes: float = Field(default=30.0, gt=0.0)

    # Passive evidence (Stage 2). ``passive_signals``: record what the accounts' normal work
    # shows (PeerFlood, freeze, a delivered first message) and fold it into a projection that
    # only SHOWS. ``passive_decisions``: let that fold write the legacy ``status`` for
    # non-clean verdicts (limited, frozen, an expired limit); off, the fold is a shadow and
    # only a real @SpamBot answer writes it. A passive clean never writes it either way.
    passive_signals: bool = True
    passive_decisions: bool = False
    # A limit with no end date is rechecked after this long.
    limited_recheck_days: float = Field(default=5.0, gt=0.0)
    # Evidence bounds: kept this long, at most this many rows per account, and the same
    # account/kind/source inside this window is one row with a count.
    evidence_retention_days: int = Field(default=30, ge=1)
    evidence_max_per_account: int = Field(default=50, ge=5)
    evidence_coalesce_minutes: float = Field(default=10.0, ge=0.0)

    # Adaptive routine interval (D2), only with ``passive_decisions``: warming asks every
    # ``warming.spam_check_interval_hours`` (72 h); an account with this many clean readings in
    # a row and no limiting evidence since is asked only every ``routine_mid_hours``, and
    # every ``routine_long_hours`` after more. Any limiting evidence resets it.
    routine_mid_hours: float = Field(default=120.0, gt=0.0)
    routine_long_hours: float = Field(default=168.0, gt=0.0)
    routine_mid_after_clean: int = Field(default=3, ge=1)
    routine_long_after_clean: int = Field(default=5, ge=1)

    # The semi-automatic appeal of a spam block (design 5.12): a HUMAN files it in the account's
    # own Telegram Web window. ``appeal_recheck_hours``: how long after the operator says it was
    # filed an open-ended limit is asked about again (a dated one: its end day plus up to
    # ``appeal_recheck_jitter_hours``), as an ordinary decision under the shared budget.
    # ``appeal_scan_minutes``: how often the recheck loop looks for due appeals.
    appeal_recheck_hours: float = Field(default=24.0, gt=0.0)
    appeal_recheck_jitter_hours: float = Field(default=4.0, ge=0.0)
    appeal_scan_minutes: float = Field(default=10.0, gt=0.0)
    # Which address opens the @SpamBot dialog in the web window: ``hash`` is WebK's own deep
    # link (``/k/#@SpamBot``), ``tgaddr`` the form Telegram's ``tg://`` links use. Neither is
    # verified live yet (docs/spam-intel.md D8): flipping this needs no code change. If the dialog
    # does not open, the window still opens on WebK and the operator searches the bot by hand.
    appeal_dialog_target: Literal["hash", "tgaddr"] = "hash"

    # Ledger and sample bounds.
    attempts_retention_days: int = Field(default=90, ge=1)
    attempts_max_rows: int = Field(default=5000, ge=100)
    samples_max_rows: int = Field(default=200, ge=1)
    sample_max_chars: int = Field(default=1000, ge=100)
    # A stored reply sample is dropped once it was last seen this long ago.
    sample_max_age_days: int = Field(default=90, ge=1)

    @model_validator(mode="after")
    def _usable_intervals(self) -> Self:
        """Every interval and range is finite, not negative, and small enough to add to a date."""
        for name, field in type(self).model_fields.items():
            if field.annotation is not float:
                continue
            value = getattr(self, name)
            if not (math.isfinite(value) and 0.0 <= value <= _MAX_INTERVAL):
                msg = f"{name} must be a finite number between 0 and {_MAX_INTERVAL:g}"
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.fleet_gap_min_seconds > self.fleet_gap_max_seconds:
            msg = "fleet_gap_min_seconds must not exceed fleet_gap_max_seconds"
            raise ValueError(msg)
        if self.fleet_soft_cap > self.fleet_hard_cap:
            msg = "fleet_soft_cap must not exceed fleet_hard_cap"
            raise ValueError(msg)
        if self.routine_mid_hours > self.routine_long_hours:
            msg = "routine_mid_hours must not exceed routine_long_hours"
            raise ValueError(msg)
        if self.routine_mid_after_clean > self.routine_long_after_clean:
            msg = "routine_mid_after_clean must not exceed routine_long_after_clean"
            raise ValueError(msg)
        if self.backoff_base_hours > self.backoff_max_hours:
            msg = "backoff_base_hours must not exceed backoff_max_hours"
            raise ValueError(msg)
        for low, high in _RANGES:
            if getattr(self, low) > getattr(self, high):
                msg = f"{low} must not exceed {high}"
                raise ValueError(msg)
        return self
