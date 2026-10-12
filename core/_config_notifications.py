"""Operator-bot notification settings — its own module for the file-size budget.

Re-exported through ``core.config`` like ``core._config_user_parser``. The bot token,
chat id and on/off switch are operator-set in the UI and stored in the database; this
module holds only the pacing and transport knobs.
"""

# aislop-ignore-file ai-slop/hardcoded-url -- the Bot API base URL is an env-overridable
# Settings default, which is what a config default is.

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class NotificationsSettings(BaseSettings):
    """Tunables for the operator bot — no magic numbers in the code."""

    model_config = SettingsConfigDict(env_prefix="NOTIFICATIONS__", extra="ignore")

    # One message per (account, kind) at most this often. In memory: one uvicorn worker.
    throttle_hours: float = Field(default=6.0, ge=0.0)
    # Whole-request deadline for one ``sendMessage`` call.
    send_timeout_seconds: float = Field(default=10.0, gt=0.0)
    bot_api_base_url: str = Field(default="https://api.telegram.org", pattern=r"^https://")
