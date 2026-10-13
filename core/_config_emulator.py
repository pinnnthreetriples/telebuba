"""The local Android container (LDPlayer 9) a mobile account can be opened in.

Split out of ``core.config`` for the file-size budget; re-exported there.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EmulatorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EMULATOR__", extra="ignore")

    # Off unless the operator turns it on: the open-mobile endpoint then answers
    # ``mobile_emulator_disabled`` and the button stays disabled.
    enabled: bool = False
    # LDPlayer's console and the adb it ships with (same folder on a stock install).
    ldconsole_path: Path = Path("C:/LDPlayer/LDPlayer9/ldconsole.exe")
    adb_path: Path = Path("C:/LDPlayer/LDPlayer9/adb.exe")
    # Installed into a container that lacks the package; refused when the file is missing.
    telegram_apk_path: Path = Path("emulator/telegram.apk")
    telegram_package: str = Field(default="org.telegram.messenger", min_length=1)
    # Container name = prefix + account id, so a container is found again by name.
    instance_prefix: str = Field(default="telebuba-", min_length=1)
    # ``width,height,dpi`` as ldconsole takes it; CPU cores and RAM of a new container.
    # Android SDK -> a clean template container of that version to copy (``{"34": "tpl-a14"}``);
    # an SDK without one is created from LDPlayer's default image and checked after boot.
    templates: dict[int, str] = Field(default_factory=dict)
    resolution: str = Field(default="720,1280,320", pattern=r"^\d{3,4},\d{3,4},\d{3}$")
    cpu_cores: int = Field(default=2, ge=1, le=8)
    memory_mb: int = Field(default=2048, ge=1024, le=8192)
    # One ldconsole/adb call; an APK install; the Android boot; one screen to appear;
    # the login code to reach the account's existing session; the screen poll interval.
    command_timeout_seconds: float = Field(default=60.0, gt=0)
    install_timeout_seconds: float = Field(default=300.0, gt=0)
    boot_timeout_seconds: float = Field(default=180.0, gt=0)
    ui_timeout_seconds: float = Field(default=30.0, gt=0)
    code_wait_seconds: float = Field(default=90.0, gt=0)
    poll_seconds: float = Field(default=2.0, gt=0)
