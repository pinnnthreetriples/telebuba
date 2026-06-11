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


class ProxySettings(BaseModel):
    check_host: str = Field(default="ip-api.com", min_length=1)
    check_path: str = Field(default="/json?fields=status,message,query,country,countryCode")
    check_port: int = Field(default=80, ge=1, le=65535)
    check_timeout_seconds: float = Field(default=8.0, gt=0)


class ProfileMediaSettings(BaseModel):
    photo_max_bytes: int = Field(default=10_000_000, ge=1)
    story_image_max_bytes: int = Field(default=10_000_000, ge=1)
    story_video_max_bytes: int = Field(default=100_000_000, ge=1)
    music_max_bytes: int = Field(default=30_000_000, ge=1)


class LoggingSettings(BaseModel):
    path: Path = Path("debug.log")
    level: str = Field(default="INFO")
    rotation: str = Field(default="10 MB")
    retention: int = Field(default=10, ge=1)
    sentry_dsn: str = ""


class Settings(BaseModel):
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    ui: UiSettings = Field(default_factory=UiSettings)
    db: DbSettings = Field(default_factory=DbSettings)
    proxy: ProxySettings = Field(default_factory=ProxySettings)
    profile_media: ProfileMediaSettings = Field(default_factory=ProfileMediaSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)


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
        proxy=ProxySettings(
            check_host=os.environ.get("PROXY__CHECK_HOST", "ip-api.com"),
            check_path=os.environ.get(
                "PROXY__CHECK_PATH",
                "/json?fields=status,message,query,country,countryCode",
            ),
            check_port=int(os.environ.get("PROXY__CHECK_PORT", "80")),
            check_timeout_seconds=float(os.environ.get("PROXY__CHECK_TIMEOUT_SECONDS", "8.0")),
        ),
        profile_media=ProfileMediaSettings(
            photo_max_bytes=int(os.environ.get("PROFILE_MEDIA__PHOTO_MAX_BYTES", "10000000")),
            story_image_max_bytes=int(
                os.environ.get("PROFILE_MEDIA__STORY_IMAGE_MAX_BYTES", "10000000"),
            ),
            story_video_max_bytes=int(
                os.environ.get("PROFILE_MEDIA__STORY_VIDEO_MAX_BYTES", "100000000"),
            ),
            music_max_bytes=int(os.environ.get("PROFILE_MEDIA__MUSIC_MAX_BYTES", "30000000")),
        ),
        logging=LoggingSettings(
            path=Path(os.environ.get("LOGGING__PATH", "debug.log")),
            level=os.environ.get("LOGGING__LEVEL", "INFO"),
            rotation=os.environ.get("LOGGING__ROTATION", "10 MB"),
            retention=int(os.environ.get("LOGGING__RETENTION", "10")),
            sentry_dsn=os.environ.get("LOGGING__SENTRY_DSN", ""),
        ),
    )


settings = load_settings()
