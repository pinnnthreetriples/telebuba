"""Bulk-message runner settings — its own module for the file-size budget.

Re-exported through ``core.config`` like ``core._config_chat_broadcast``. What the
operator tunes per run (pauses, caps, schedule, protection) lives in the run request;
this module holds the runner's own reaction to a refusal that sent nothing.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BulkMessagesSettings(BaseSettings):
    """Tunables for the bulk-message runner — no magic numbers in the code."""

    model_config = SettingsConfigDict(env_prefix="BULK_MESSAGES__", extra="ignore")

    # A dropped connection or a slow mode sent nothing and says nothing about the
    # account: it waits this long (or the slow mode's own wait) and tries the same
    # recipient again, instead of leaving the run.
    transient_retry_seconds: float = Field(default=60.0, gt=0.0)
    # Refusals like that in a row before the account does leave the run after all.
    max_transient_retries: int = Field(default=3, ge=0)
