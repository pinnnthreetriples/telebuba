"""Contracts for native mobile device emulation and Telegram Android launcher."""

from __future__ import annotations

from pydantic import BaseModel, Field


class OpenMobileResult(BaseModel):
    """The outcome of launching the Telegram Android emulator for the account."""

    launched: bool
    signed_in: bool
    instance_name: str
    message: str | None = None


class EmulatorConfig(BaseModel):
    """Configuration for an emulator instance."""

    name: str = Field(min_length=1)
    model: str = "Samsung Galaxy S23"
    imei: str | None = None
    android_id: str | None = None
    mac: str | None = None
    width: int = Field(default=412, ge=200, le=2560)
    height: int = Field(default=915, ge=200, le=2560)
    dpi: int = Field(default=320, ge=100, le=640)
    cpus: int = Field(default=1, ge=1, le=16)
    ram_mb: int = Field(default=1536, ge=512, le=16384)
