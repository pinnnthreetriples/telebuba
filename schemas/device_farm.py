"""Contracts for the device farm: the phones, and the commands their web screen takes.

A phone is the Android pod a mobile identity lives in. An ``account`` phone carries one
Telegram account and is named after it; a ``standalone`` one (an independent emulator)
carries none and has a pool proxy of its own. The contract lists the phone's apps, so a
phone that later carries other platforms keeps its shape. No behaviour, no I/O.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from schemas.emulator import EmulatorRefusalCode  # noqa: TC001
from schemas.standalone_emulator import StandaloneApp  # noqa: TC001

# ``asleep``: no container running (``created`` says whether it exists at all);
# ``starting``: a start is booting it or signing its app in; ``free``: running, idle;
# ``warming``: an activity session drives it; ``manual``: an operator has its controls.
PhoneStatus = Literal["asleep", "starting", "free", "warming", "manual"]
PhonePlatform = Literal["telegram"]
PhoneKind = Literal["account", "standalone"]
# ``view`` streams the screen only (scrcpy runs without its control socket);
# ``control`` adds the operator's touch, keys and clipboard.
ScreenMode = Literal["view", "control"]

# Stable codes a screen socket closes with; the SPA translates them.
ScreenCloseCode = Literal[
    "screen_ended",
    "screen_superseded",
    "screen_busy",
    "screen_unavailable",
    "screen_bad_command",
]

PHONE_ID_PATTERN = r"^[A-Za-z0-9_.-]{1,128}$"
_INJECT_TEXT_MAX = 300
_CLIPBOARD_MAX = 65536
_SCREEN_SIDE_MAX = 65535


class PhoneApp(BaseModel):
    """One account an app on the phone is signed in as."""

    platform: PhonePlatform
    account_id: str = Field(min_length=1)
    label: str | None = None
    username: str | None = None


class PhoneProxy(BaseModel):
    """Where the phone's traffic goes; never its credentials."""

    proxy_type: str | None = None
    host: str | None = None
    port: int | None = None
    country_code: str | None = None
    status: str | None = None


class Phone(BaseModel):
    phone_id: str = Field(pattern=PHONE_ID_PATTERN)
    kind: PhoneKind = "account"
    # The operator's name for a standalone phone; an account phone's is its app's label.
    label: str | None = None
    created: bool
    status: PhoneStatus
    # How the operator reads the phone ("Samsung", "Galaxy S23 Ultra"); ``device_model``
    # is what Telegram is told ("samsungSM-S918B").
    maker: str
    model_name: str
    device_model: str
    system_version: str
    app_version: str
    lang_code: str
    proxy: PhoneProxy | None = None
    # The pool proxy the phone is on, for the picker that changes it.
    proxy_id: str | None = None
    apps: list[PhoneApp]
    # What a standalone phone's start installs when missing; empty for an account phone.
    install_apps: list[StandaloneApp] = Field(default_factory=list)


class PhoneList(BaseModel):
    # ``False``: nothing can start — ``problem`` says why (the emulator switched off,
    # or docker not answering); the phones still show, all of them asleep.
    enabled: bool
    problem: EmulatorRefusalCode | None = None
    phones: list[Phone]


class PhoneActionResult(BaseModel):
    phone_id: str = Field(pattern=PHONE_ID_PATTERN)
    status: PhoneStatus


class _Point(BaseModel):
    """A point on the video frame the viewer shows, with that frame's size.

    scrcpy drops an event whose frame size is not the one it is encoding at, so a
    stale click after a rotation is lost rather than landing somewhere else.
    """

    x: int = Field(ge=0, le=_SCREEN_SIDE_MAX)
    y: int = Field(ge=0, le=_SCREEN_SIDE_MAX)
    width: int = Field(ge=1, le=_SCREEN_SIDE_MAX)
    height: int = Field(ge=1, le=_SCREEN_SIDE_MAX)


class ScreenTouch(_Point):
    type: Literal["touch"]
    action: Literal["down", "move", "up"]


class ScreenScroll(_Point):
    type: Literal["scroll"]
    dx: float = Field(ge=-16, le=16)
    dy: float = Field(ge=-16, le=16)


class ScreenKey(BaseModel):
    """An Android keycode (``KEYCODE_*``), e.g. 3 Home, 4 Back, 187 recent apps."""

    type: Literal["key"]
    action: Literal["down", "up"]
    keycode: int = Field(ge=0, le=400)
    meta: int = Field(default=0, ge=0, le=0x7FFFFFFF)


class ScreenText(BaseModel):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=_INJECT_TEXT_MAX)


class ScreenClipboardSet(BaseModel):
    """Put text on the phone's clipboard; ``paste`` also pastes it where the focus is."""

    type: Literal["clipboard_set"]
    text: str = Field(max_length=_CLIPBOARD_MAX)
    paste: bool = False


class ScreenClipboardGet(BaseModel):
    """Ask for the phone's clipboard; it comes back as a ``clipboard`` message."""

    type: Literal["clipboard_get"]


ScreenCommand = Annotated[
    ScreenTouch | ScreenScroll | ScreenKey | ScreenText | ScreenClipboardSet | ScreenClipboardGet,
    Field(discriminator="type"),
]


class ScreenClipboard(BaseModel):
    """The phone's clipboard, sent to the viewer as a text frame."""

    type: Literal["clipboard"] = "clipboard"
    text: str
