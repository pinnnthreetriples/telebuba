from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field


class Settings(BaseModel):
    telegram_api_id: int = Field(default=0, ge=0)
    telegram_api_hash: str = ""
    database_path: Path = Path("telebuba.db")
    session_dir: Path = Path("sessions")
    ui_port: int = Field(default=8080, ge=1, le=65535)
    telegram_timeout_seconds: int = Field(default=20, ge=1)
    telegram_connection_retries: int = Field(default=3, ge=0)
    telegram_retry_delay_seconds: int = Field(default=2, ge=0)
    telegram_request_retries: int = Field(default=3, ge=0)
    # Logging — flat for now; nested namespaces in issue #6.
    log_path: Path = Path("debug.log")
    log_level: str = Field(default="INFO")
    log_rotation: str = Field(default="10 MB")
    log_retention: int = Field(default=10, ge=1)
    sentry_dsn: str = ""


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        telegram_api_id=int(os.environ.get("TELEGRAM_API_ID", "0")),
        telegram_api_hash=os.environ.get("TELEGRAM_API_HASH", ""),
        database_path=Path(os.environ.get("TELEBUBA_DB_PATH", "telebuba.db")),
        session_dir=Path(os.environ.get("TELEBUBA_SESSION_DIR", "sessions")),
        ui_port=int(os.environ.get("TELEBUBA_PORT", "8080")),
        telegram_timeout_seconds=int(os.environ.get("TELEGRAM_TIMEOUT_SECONDS", "20")),
        telegram_connection_retries=int(os.environ.get("TELEGRAM_CONNECTION_RETRIES", "3")),
        telegram_retry_delay_seconds=int(os.environ.get("TELEGRAM_RETRY_DELAY_SECONDS", "2")),
        telegram_request_retries=int(os.environ.get("TELEGRAM_REQUEST_RETRIES", "3")),
        log_path=Path(os.environ.get("TELEBUBA_LOG_PATH", "debug.log")),
        log_level=os.environ.get("TELEBUBA_LOG_LEVEL", "INFO"),
        log_rotation=os.environ.get("TELEBUBA_LOG_ROTATION", "10 MB"),
        log_retention=int(os.environ.get("TELEBUBA_LOG_RETENTION", "10")),
        sentry_dsn=os.environ.get("SENTRY_DSN", ""),
    )


settings = load_settings()
