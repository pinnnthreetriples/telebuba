"""The local Android container (redroid under Docker) a mobile account can be opened in.

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
    # redroid needs a kernel with binder (docs/redroid.md). Point ``adb_path`` at the
    # adb scrcpy ships with: two adb versions on one host keep killing each other's server.
    adb_path: Path = Path("adb")
    # The docker CLI, the scrcpy viewer, the image per Android SDK, and the host port
    # of container index 0's adb (index N listens on that port + N, on 127.0.0.1 only).
    docker_path: Path = Path("docker")
    scrcpy_path: Path = Path("scrcpy")
    redroid_images: dict[int, str] = Field(
        default_factory=lambda: {
            33: "redroid/redroid:13.0.0-latest",
            34: "redroid/redroid:14.0.0-latest",
        }
    )
    redroid_first_adb_port: int = Field(default=5600, ge=1024, le=65000)
    # Installed into a container that lacks the package; refused when the file is missing.
    telegram_apk_path: Path = Path("emulator/telegram.apk")
    telegram_package: str = Field(default="org.telegram.messenger", min_length=1)
    # Container name = prefix + account id, so a container is found again by name.
    instance_prefix: str = Field(default="telebuba-", min_length=1)
    # ``width,height,dpi`` of the screen; CPU cores and RAM of a new container.
    resolution: str = Field(default="720,1280,320", pattern=r"^\d{3,4},\d{3,4},\d{3}$")
    cpu_cores: int = Field(default=2, ge=1, le=8)
    memory_mb: int = Field(default=2048, ge=1024, le=8192)
    # One docker/adb call; an APK install; the Android boot; one screen to appear;
    # the login code to reach the account's existing session; the screen poll interval.
    command_timeout_seconds: float = Field(default=60.0, gt=0)
    install_timeout_seconds: float = Field(default=300.0, gt=0)
    boot_timeout_seconds: float = Field(default=180.0, gt=0)
    ui_timeout_seconds: float = Field(default=30.0, gt=0)
    code_wait_seconds: float = Field(default=90.0, gt=0)
    poll_seconds: float = Field(default=2.0, gt=0)
