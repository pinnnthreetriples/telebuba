"""Instance settings: which computer owns the fleet. Re-exported through ``core.config``."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class InstanceSettings(BaseSettings):
    """The one computer allowed to connect the fleet's accounts to Telegram."""

    model_config = SettingsConfigDict(env_prefix="INSTANCE__", extra="ignore")

    # The fleet owner's hostname. Set it on EVERY computer that holds copies of the session
    # files; only the one with this hostname connects to Telegram. Empty: no restriction.
    fleet_owner_host: str = ""
