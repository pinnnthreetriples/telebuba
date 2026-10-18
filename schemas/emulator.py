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
    "emulator_not_running",
    "emulator_not_signed_in",
    "emulator_capacity",
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


# What an activity session meant to do but the screen did not show: no channel row in
# the chat list, no stories row, no post to react to. Skipped, never guessed at.
PlaybookSkip = Literal["no_channel", "no_stories", "no_post"]


class PlaybookPlan(BaseModel):
    """What one activity session may do: the warming visit's shape and the account's switches.

    ``channels`` caps the channels opened (``None``: as many as time allows),
    ``reaction_probability`` is the chance of one reaction per channel read (0 turns
    reactions off), ``max_actions`` caps channels read plus reactions (``None``: no cap).
    """

    channels: int | None = Field(default=None, ge=0)
    reaction_probability: float = Field(default=0.15, ge=0.0, le=1.0)
    stories: bool = True
    stories_first: bool = False
    max_actions: int | None = Field(default=None, ge=0)


class PlaybookResult(BaseModel):
    """What one activity session in the container's app did."""

    channels_read: int = Field(default=0, ge=0)
    scrolls: int = Field(default=0, ge=0)
    stories_watched: int = Field(default=0, ge=0)
    reactions: int = Field(default=0, ge=0)
    skipped: list[PlaybookSkip] = Field(default_factory=list)
