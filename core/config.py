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
    telegram_timeout_seconds: int = Field(default=20, ge=1)
    telegram_connection_retries: int = Field(default=3, ge=0)
    telegram_retry_delay_seconds: int = Field(default=2, ge=0)
    telegram_request_retries: int = Field(default=3, ge=0)


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        telegram_api_id=int(os.environ.get("TELEGRAM_API_ID", "0")),
        telegram_api_hash=os.environ.get("TELEGRAM_API_HASH", ""),
        database_path=Path(os.environ.get("TELEBUBA_DB_PATH", "telebuba.db")),
        session_dir=Path(os.environ.get("TELEBUBA_SESSION_DIR", "sessions")),
        telegram_timeout_seconds=int(os.environ.get("TELEGRAM_TIMEOUT_SECONDS", "20")),
        telegram_connection_retries=int(os.environ.get("TELEGRAM_CONNECTION_RETRIES", "3")),
        telegram_retry_delay_seconds=int(os.environ.get("TELEGRAM_RETRY_DELAY_SECONDS", "2")),
        telegram_request_retries=int(os.environ.get("TELEGRAM_REQUEST_RETRIES", "3")),
    )


settings = load_settings()
