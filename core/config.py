"""Project settings — typed pydantic-settings, one nested namespace per domain.

Env keys use a double-underscore separator: ``TELEGRAM__API_ID``,
``LOGGING__SENTRY_DSN``, ``WARMING__REACTION_PROBABILITY``, etc.

Validation runs at import time. A misconfigured ``.env`` raises a clear
``ValidationError`` instead of producing a half-initialised app with silent
defaults.

See ``.env.example`` for the full list of supported keys.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TELEGRAM__", extra="ignore")

    api_id: int = Field(default=0, ge=0)
    api_hash: str = ""
    session_dir: Path = Path("sessions")
    timeout_seconds: int = Field(default=20, ge=1)
    connection_retries: int = Field(default=3, ge=0)
    retry_delay_seconds: int = Field(default=2, ge=0)
    request_retries: int = Field(default=3, ge=0)


class UiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UI__", extra="ignore")

    port: int = Field(default=8080, ge=1, le=65535)


class DbSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB__", extra="ignore")

    path: Path = Path("telebuba.db")


class ProxySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROXY__", extra="ignore")

    check_host: str = Field(default="ip-api.com", min_length=1)
    check_path: str = Field(default="/json?fields=status,message,query,country,countryCode")
    check_port: int = Field(default=80, ge=1, le=65535)
    check_timeout_seconds: float = Field(default=8.0, gt=0)


class ProfileMediaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROFILE_MEDIA__", extra="ignore")

    photo_max_bytes: int = Field(default=10_000_000, ge=1)
    story_image_max_bytes: int = Field(default=10_000_000, ge=1)
    story_video_max_bytes: int = Field(default=100_000_000, ge=1)
    music_max_bytes: int = Field(default=30_000_000, ge=1)
    # .session files = effective credentials. Cap to deter accidental large uploads.
    session_max_bytes: int = Field(default=5_000_000, ge=1)


class LoggingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOGGING__", extra="ignore")

    path: Path = Path("debug.log")
    level: str = Field(default="INFO")
    rotation: str = Field(default="10 MB")
    retention: int = Field(default=10, ge=1)
    sentry_dsn: str = ""


class WarmingSettings(BaseSettings):
    """Tunables for the warming engine — all delays/limits live here, no magic numbers."""

    model_config = SettingsConfigDict(env_prefix="WARMING__", extra="ignore")

    action_delay_min_seconds: float = Field(default=10.0, ge=0.0)
    action_delay_max_seconds: float = Field(default=30.0, ge=0.0)
    typing_min_seconds: float = Field(default=5.0, ge=0.0)
    typing_max_seconds: float = Field(default=30.0, ge=0.0)
    reading_min_seconds: float = Field(default=8.0, ge=0.0)
    reading_max_seconds: float = Field(default=45.0, ge=0.0)
    cycle_sleep_min_hours: float = Field(default=12.0, ge=0.0)
    cycle_sleep_max_hours: float = Field(default=30.0, ge=0.0)
    startup_jitter_max_seconds: float = Field(default=8.0, ge=0.0)
    channels_per_cycle_min: int = Field(default=1, ge=1)
    channels_per_cycle_max: int = Field(default=3, ge=1)
    reaction_probability: float = Field(default=0.6, ge=0.0, le=1.0)
    read_message_limit: int = Field(default=15, ge=1, le=100)
    reaction_message_limit: int = Field(default=20, ge=1, le=100)
    default_reactions: list[str] = Field(
        default_factory=lambda: ["👍", "🔥", "❤️", "😁", "🎉", "👏", "🤔", "🙏"],
    )
    # Channel guardrails. Service layer enforces these limits.
    max_channels_total: int = Field(default=500, ge=1)
    max_channels_per_add: int = Field(default=50, ge=1)
    max_channel_length: int = Field(default=120, ge=1)
    # Gemini DM payload guardrails — protect the recipient from junk output.
    chat_message_max_chars: int = Field(default=300, ge=1)
    chat_message_max_lines: int = Field(default=4, ge=1)
    # Graceful stop budget when cancelling a per-account loop task.
    stop_cancel_timeout_seconds: float = Field(default=5.0, ge=0.1)


class GeminiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEMINI__", extra="ignore")

    api_key: str = ""
    model: str = Field(default="gemini-2.5-flash")
    base_url: str = Field(default="https://generativelanguage.googleapis.com/v1beta")
    timeout_seconds: float = Field(default=30.0, ge=1.0)
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=120, ge=1, le=2048)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    ui: UiSettings = Field(default_factory=UiSettings)
    db: DbSettings = Field(default_factory=DbSettings)
    proxy: ProxySettings = Field(default_factory=ProxySettings)
    profile_media: ProfileMediaSettings = Field(default_factory=ProfileMediaSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    warming: WarmingSettings = Field(default_factory=WarmingSettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)


def load_settings() -> Settings:
    """Load + validate settings. Each nested model reads its own env prefix."""
    # Loading .env happens once via pydantic-settings dotenv source when present.
    # We still trigger an explicit dotenv load to support the case where the test
    # suite mutates os.environ after import (matches the pre-refactor behaviour).
    from dotenv import load_dotenv  # noqa: PLC0415 - keep import-time side-effects bounded.

    load_dotenv(override=False)
    return Settings()


settings = load_settings()
