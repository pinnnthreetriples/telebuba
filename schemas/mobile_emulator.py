"""Pydantic models for the native mobile emulator (LDPlayer 9 + ADB) domain."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MobileEmulatorRefusalCode = Literal[
    "mobile_emulator_no_proxy",
    "mobile_emulator_no_profile",
    "mobile_emulator_not_found",
    "mobile_emulator_code_timeout",
    "mobile_emulator_launch_failed",
    "mobile_emulator_shutting_down",
]


class OpenMobileResult(BaseModel):
    """Outcome of opening (or raising) an account's mobile Telegram emulator."""

    launched: bool
    signed_in: bool
    instance_name: str = Field(min_length=1)
    message: str | None = None
