"""User-parser settings — its own module for the file-size budget.

Re-exported through ``core.config`` like ``core._config_chat_broadcast``. The pauses the
operator sets live in each run's request; this module holds the floors under them, the
fleet ceilings and the run's own failure rules.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class UserParserSettings(BaseSettings):
    """Tunables for the user parser — no magic numbers in the code."""

    model_config = SettingsConfigDict(env_prefix="USER_PARSER__", extra="ignore")

    max_sources_per_run: int = Field(default=100, ge=1, le=200)
    max_accounts_per_run: int = Field(default=20, ge=1, le=50)
    # Floors under the operator's pauses: "fast" shortens them, never below these.
    min_request_delay_seconds: float = Field(default=0.5, ge=0.0)
    min_chat_delay_seconds: float = Field(default=1.0, ge=0.0)
    # What "fast" multiplies both pauses by.
    fast_factor: float = Field(default=0.5, gt=0.0, le=1.0)
    # "Protect" jitters every pause by up to this share either way.
    jitter: float = Field(default=0.3, ge=0.0, lt=1.0)
    # Reads in a row an account may fail before it leaves the run.
    max_consecutive_errors: int = Field(default=3, ge=1)
    # A flood up to this long is sat out and the account comes back; a longer one takes
    # it out of the run (its cooldown still lands fleet-wide either way).
    flood_sit_out_max_seconds: int = Field(default=300, ge=0)
    # A rate limit Telegram gave no duration for (peer flood) parks the account this long.
    peer_flood_cooldown_seconds: int = Field(default=3600, ge=1)
    # Joins of private invite chats: rolling-24h per account out of the shared join log
    # (0 = no cap), and the jittered gap between two joins of one account.
    max_joins_per_account_per_day: int = Field(default=20, ge=0)
    join_gap_seconds: float = Field(default=30.0, ge=0.0)
    # Shape of that gap's human-like (log-normal) jitter, as chat broadcast draws it.
    join_lognorm_mu: float = -0.8
    join_lognorm_sigma: float = Field(default=0.6, gt=0.0)
    # Rows per page the export streams, and per page the modal may ask for.
    export_chunk_rows: int = Field(default=1000, ge=1)
    max_page_rows: int = Field(default=500, ge=1)
