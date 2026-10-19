"""Contracts for opening a mobile account in the local Android container (redroid under Docker).

The container is the account's MOBILE device made visible: its manufacturer, model and
SDK are the mobile fingerprint's, and its pod's gateway sends all of its traffic through
the account's SOCKS5 proxy. No behaviour, no I/O.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

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
    "emulator_scrcpy_missing",
    "emulator_download_failed",
    "emulator_checksum_mismatch",
    "emulator_image_unavailable",
    "emulator_docker_unavailable",
    "emulator_adb_missing",
    "emulator_binder_missing",
    "emulator_preparing",
    "emulator_screen_timeout",
]

# A search query ``input text`` can type: 2-32 letters, digits and spaces.
_TYPEABLE = r"^[A-Za-z0-9][A-Za-z0-9 ]{1,31}$"

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
# the chat list, no stories row, no post to react to, no search button or result, no
# photo or note to play, no folder to add, no Mini App. Skipped, never guessed at.
# ``app_error`` (an error dialog: too many attempts, too many communities) and
# ``app_offline`` (the app kept waiting for the network) end the session early.
PlaybookSkip = Literal[
    "no_channel",
    "no_stories",
    "no_post",
    "no_search",
    "no_media",
    "no_folder",
    "no_webapp",
    "app_error",
    "app_offline",
]


class PlaybookPlan(BaseModel):
    """What one activity session may do: the warming visit's shape and the account's switches.

    ``channels`` caps the channels opened (``None``: as many as time allows),
    ``reaction_probability`` is the chance of one reaction per channel read (0 turns
    reactions off), ``max_actions`` caps channels read plus reactions (``None``: no cap).

    The scenarios a session mixes in are off unless asked for: ``media`` opens a photo
    or plays a note in a channel read, each of ``search_queries`` is typed into the
    global search once (printable ASCII: ``input text`` types nothing else). Account
    changes have switches of their own: ``join_channels`` joins (and sometimes mutes)
    a channel the search opened, ``folders`` adds a recommended folder or creates
    ``folder_name``, and ``webapp_bot`` names the bot whose Mini App is opened.
    """

    channels: int | None = Field(default=None, ge=0)
    reaction_probability: float = Field(default=0.15, ge=0.0, le=1.0)
    stories: bool = True
    stories_first: bool = False
    max_actions: int | None = Field(default=None, ge=0)
    media: bool = False
    search_queries: list[Annotated[str, StringConstraints(pattern=_TYPEABLE)]] = Field(
        default_factory=list
    )
    join_channels: bool = False
    folders: bool = False
    # Telegram caps a folder's name at 12 characters.
    folder_name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ]{0,11}$")
    webapp_bot: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{3,31}$")


class PlaybookResult(BaseModel):
    """What one activity session in the container's app did."""

    channels_read: int = Field(default=0, ge=0)
    scrolls: int = Field(default=0, ge=0)
    stories_watched: int = Field(default=0, ge=0)
    reactions: int = Field(default=0, ge=0)
    searches_done: int = Field(default=0, ge=0)
    media_consumed: int = Field(default=0, ge=0)
    channels_joined: int = Field(default=0, ge=0)
    folders_created: int = Field(default=0, ge=0)
    webapps_opened: int = Field(default=0, ge=0)
    skipped: list[PlaybookSkip] = Field(default_factory=list)
