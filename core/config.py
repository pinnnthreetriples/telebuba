"""Project settings — nested Pydantic models, one namespace per domain.

Env keys use a double-underscore separator: ``TELEGRAM__API_ID``,
``LOGGING__SENTRY_DSN``, etc. The convention matches the one ``pydantic-settings``
uses by default, so a future migration to ``BaseSettings`` stays trivial.

See ``.env.example`` for the full list of supported keys.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field


class TelegramSettings(BaseModel):
    api_id: int = Field(default=0, ge=0)
    api_hash: str = ""
    session_dir: Path = Path("sessions")
    timeout_seconds: int = Field(default=20, ge=1)
    connection_retries: int = Field(default=3, ge=0)
    retry_delay_seconds: int = Field(default=2, ge=0)
    request_retries: int = Field(default=3, ge=0)


class UiSettings(BaseModel):
    port: int = Field(default=8080, ge=1, le=65535)


class DbSettings(BaseModel):
    path: Path = Path("telebuba.db")


class LoggingSettings(BaseModel):
    path: Path = Path("debug.log")
    level: str = Field(default="INFO")
    rotation: str = Field(default="10 MB")
    retention: int = Field(default=10, ge=1)
    sentry_dsn: str = ""


class WarmingSettings(BaseModel):
    """Tunables for the warming engine — all delays/limits live here, no magic numbers."""

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


class GeminiSettings(BaseModel):
    api_key: str = ""
    model: str = Field(default="gemini-2.5-flash")
    base_url: str = Field(default="https://generativelanguage.googleapis.com/v1beta")
    timeout_seconds: float = Field(default=30.0, ge=1.0)
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=120, ge=1, le=2048)


class Settings(BaseModel):
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    ui: UiSettings = Field(default_factory=UiSettings)
    db: DbSettings = Field(default_factory=DbSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    warming: WarmingSettings = Field(default_factory=WarmingSettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        telegram=TelegramSettings(
            api_id=int(os.environ.get("TELEGRAM__API_ID", "0")),
            api_hash=os.environ.get("TELEGRAM__API_HASH", ""),
            session_dir=Path(os.environ.get("TELEGRAM__SESSION_DIR", "sessions")),
            timeout_seconds=int(os.environ.get("TELEGRAM__TIMEOUT_SECONDS", "20")),
            connection_retries=int(os.environ.get("TELEGRAM__CONNECTION_RETRIES", "3")),
            retry_delay_seconds=int(os.environ.get("TELEGRAM__RETRY_DELAY_SECONDS", "2")),
            request_retries=int(os.environ.get("TELEGRAM__REQUEST_RETRIES", "3")),
        ),
        ui=UiSettings(port=int(os.environ.get("UI__PORT", "8080"))),
        db=DbSettings(path=Path(os.environ.get("DB__PATH", "telebuba.db"))),
        logging=LoggingSettings(
            path=Path(os.environ.get("LOGGING__PATH", "debug.log")),
            level=os.environ.get("LOGGING__LEVEL", "INFO"),
            rotation=os.environ.get("LOGGING__ROTATION", "10 MB"),
            retention=int(os.environ.get("LOGGING__RETENTION", "10")),
            sentry_dsn=os.environ.get("LOGGING__SENTRY_DSN", ""),
        ),
        gemini=GeminiSettings(
            api_key=os.environ.get("GEMINI__API_KEY", ""),
            model=os.environ.get("GEMINI__MODEL", "gemini-2.5-flash"),
        ),
    )


settings = load_settings()
