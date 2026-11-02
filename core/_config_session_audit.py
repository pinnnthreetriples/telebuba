"""Session audit settings: the pause between ending one foreign session and the next.

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
