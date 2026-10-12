"""Contracts for an account's devices and its second, separate authorization.

An account has a desktop device, a mobile device, or both; "both" is two Telegram
authorizations (two ``.session`` files, two fingerprints), never one session that
presents two fingerprints. The second one signs in with a login code the account's
existing session reads from Telegram's service chat.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from schemas.device_fingerprint import DeviceType  # noqa: TC001 - Pydantic field type

# Stable codes the API returns verbatim and the SPA translates (``shell.code.*``).
DeviceRefusalCode = Literal[
    "device_missing",
    "mobile_api_not_configured",
    "device_already_present",
    "device_phone_unknown",
    "device_session_conflict",
    "device_code_cooldown",
    "device_code_timeout",
    "device_code_invalid",
    "device_twofa_missing",
    "device_twofa_invalid",
    "device_user_mismatch",
    "device_flood_wait",
    "device_lang_pack_invalid",
    "device_primary_unavailable",
    "device_auth_failed",
]

# What the operator picks when adding an account: the device the imported or
# signed-in session IS, and whether to add the other device as a second sign-in.
DeviceChoice = Literal["desktop", "mobile", "both"]


class SecondDeviceSignIn(BaseModel):
    """Gateway input: sign ``device_type`` in as a new, separate authorization."""

    account_id: str = Field(min_length=1)
    device_type: DeviceType
    # The new ``.session`` file (a plain child of the session directory).
    session_name: str = Field(min_length=1)


class SecondDeviceResult(BaseModel):
    """Gateway output: ``code`` is ``None`` on success, else why nothing was kept."""

    account_id: str = Field(min_length=1)
    device_type: DeviceType
    code: DeviceRefusalCode | None = None
    # Bounded diagnostics only: an exception class name, never its text.
    error_type: str | None = None
    flood_wait_seconds: int | None = Field(default=None, ge=0)


class AddDeviceRequest(BaseModel):
    """API body for ``POST /accounts/{id}/devices``."""

    device_type: DeviceType
