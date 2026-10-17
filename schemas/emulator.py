"""Contracts for opening a mobile account in the local Android container (redroid under Docker).

The container is the account's MOBILE device made visible: its manufacturer, model and
SDK are the mobile fingerprint's, and its pod's gateway sends all of its traffic through
the account's SOCKS5 proxy. No behaviour, no I/O.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Stable codes the API returns verbatim and the SPA translates (``shell.code.*``).
EmulatorRefusalCode = Literal[
    "mobile_emulator_disabled",
    "emulator_busy",
    "emulator_not_found",
    "emulator_command_failed",
    "emulator_command_timeout",
    "emulator_boot_timeout",
    "emulator_sdk_mismatch",
    "emulator_device_unknown",
    "emulator_apk_missing",
    "emulator_install_failed",
    "emulator_proxy_missing",
    "emulator_proxy_unsupported",
    "emulator_text_unsupported",
    "emulator_field_missing",
    "emulator_phone_invalid",
    "emulator_code_timeout",
    "emulator_twofa_missing",
]

# ``started``: the container was booted (and signed in when it could be);
# ``already_running``: it was up already on the current proxy, so it was not booted or
# reconfigured again (only signed in where it stood, when its app showed the intro or
# phone screen).
OpenMobileStatus = Literal["started", "already_running"]


class OpenMobileResult(BaseModel):
    """What opening the container did; ``signed_in`` is read off the screen, never cached."""

    account_id: str = Field(min_length=1)
    status: OpenMobileStatus
    signed_in: bool


class EmulatorStatus(BaseModel):
    """Whether the container feature is switched on (the SPA disables the button if not)."""

    enabled: bool
