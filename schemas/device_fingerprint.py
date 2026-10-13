from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DevicePlatform = Literal["windows", "macos", "linux", "android"]
# One Telegram authorization per device: an account has a desktop session, a mobile
# session, or both — never one session presenting two fingerprints.
DeviceType = Literal["desktop", "mobile"]


def other_device(device_type: DeviceType) -> DeviceType:
    """The device type an account's second authorization would be."""
    return "mobile" if device_type == "desktop" else "desktop"


def desktop_only() -> list[DeviceType]:
    """The device list of every account that never added a second device."""
    return ["desktop"]


def account_devices(
    session_device: DeviceType,
    second_session_name: str | None,
) -> list[DeviceType]:
    """The account's devices, primary first: desktop when it has one, else mobile."""
    devices: list[DeviceType] = [session_device]
    if second_session_name is not None:
        devices.append(other_device(session_device))
    return sorted(devices, key=lambda device: device != "desktop")


class DeviceFingerprint(BaseModel):
    account_id: str = Field(min_length=1)
    device_type: DeviceType = "desktop"
    platform: DevicePlatform
    device_model: str = Field(min_length=1)
    system_version: str = Field(min_length=1)
    app_version: str = Field(min_length=1)
    lang_code: str = Field(min_length=1)
    system_lang_code: str = Field(min_length=1)


class TelegramClientRequest(BaseModel):
    account_id: str = Field(min_length=1)
    # ``None`` = the account's primary device (desktop when it has one).
    device_type: DeviceType | None = None
    session_name: str | None = Field(default=None, min_length=1)
    receive_updates: bool = True


class TelegramClientProfile(BaseModel):
    account_id: str = Field(min_length=1)
    device_type: DeviceType = "desktop"
    session_path: str = Field(min_length=1)
    receive_updates: bool
    device: DeviceFingerprint
    proxy_type: str | None = None
    proxy_host: str | None = None
    proxy_port: int | None = Field(default=None, ge=1, le=65_535)
    proxy_username: str | None = None
    proxy_password: str | None = None
