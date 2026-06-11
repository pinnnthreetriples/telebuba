from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DevicePlatform = Literal["windows", "macos", "linux"]


class DeviceFingerprint(BaseModel):
    account_id: str = Field(min_length=1)
    platform: DevicePlatform
    device_model: str = Field(min_length=1)
    system_version: str = Field(min_length=1)
    app_version: str = Field(min_length=1)
    lang_code: str = Field(min_length=1)
    system_lang_code: str = Field(min_length=1)


class TelegramClientRequest(BaseModel):
    account_id: str = Field(min_length=1)
    session_name: str | None = Field(default=None, min_length=1)
    receive_updates: bool = True


class TelegramClientProfile(BaseModel):
    account_id: str = Field(min_length=1)
    session_path: str = Field(min_length=1)
    receive_updates: bool
    device: DeviceFingerprint
    proxy_type: str | None = None
    proxy_host: str | None = None
    proxy_port: int | None = Field(default=None, ge=1, le=65_535)
    proxy_username: str | None = None
    proxy_password: str | None = None
