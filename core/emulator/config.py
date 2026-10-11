"""Configuration for the local LDPlayer 9 Android emulator and ADB bridge."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

_DEFAULT_LDPLAYER_DIR = Path(r"C:\LDPlayer\LDPlayer9")


class EmulatorSettings(BaseModel):
    """Runtime settings for LDPlayer 9 CLI (`ldconsole.exe`) and `adb.exe`."""

    ldconsole_path: Path = Field(
        default=_DEFAULT_LDPLAYER_DIR / "ldconsole.exe",
    )
    adb_path: Path = Field(
        default=_DEFAULT_LDPLAYER_DIR / "adb.exe",
    )
    telegram_apk_path: Path = Field(
        default=Path("data/apks/Telegram.apk"),
    )
    screen_width: int = Field(default=412, ge=320, le=1440)
    screen_height: int = Field(default=915, ge=480, le=3200)
    screen_dpi: int = Field(default=320, ge=120, le=640)
    cpus: int = Field(default=1, ge=1, le=8)
    ram_mb: int = Field(default=1536, ge=512, le=8192)
    boot_timeout_seconds: float = Field(default=60.0, gt=0.0)
    login_code_timeout_seconds: float = Field(default=45.0, gt=0.0)


emulator_settings = EmulatorSettings()
