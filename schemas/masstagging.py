"""Pydantic contracts for the masstagging domain («Масстегинг»).

Two modes: an account posts a story that @mentions people (the person gets a "mentioned
you in a story" push), or it only flicks the auto-delete timer in the person's DM
(priming — two service lines in the chat). This module carries the campaign, its
settings and the requests; the board lives in ``schemas.masstagging_board``. No
behaviour, no I/O.

``MasstaggingRefusalCode`` is declared whole so ``tests/test_error_code_i18n_parity.py``
can hold the SPA's ``shell.code.*`` table to it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The ranges are the chat broadcast's own: one OpenAPI name each, one shape.
from schemas.chat_broadcast import MinutesRange, SecondsRange

MasstaggingMode = Literal["stories", "priming"]
# ``draft``/``ready`` are derived from the settings of a campaign never started, and
# ``resting`` from a run waiting for tomorrow; the rest are the run's own.
MasstaggingStatus = Literal["draft", "ready", "running", "paused", "resting", "done", "failed"]
MasstaggingTargetSource = Literal["manual", "contacts", "base"]
MasstaggingVisibility = Literal["all", "contacts"]
MasstaggingStopMode = Literal["count", "time"]
StoryLifetimeHours = Literal[6, 12, 24, 48]
# What keeps a campaign from starting, in the order the operator would fix it.
MasstaggingMissing = Literal["no_accounts", "all_spamblocked", "no_targets", "no_media"]

MasstaggingRefusalCode = Literal[
    "campaign_running",
    "campaign_changed",
    "account_not_found",
    "too_many_targets",
    "masstagging_no_accounts",
    "masstagging_no_free_accounts",
    "masstagging_no_targets",
    "masstagging_no_media",
    "masstagging_invalid_target",
    "masstagging_too_many_accounts",
    "masstagging_too_many_media",
    "masstagging_target_not_found",
    "masstagging_target_state_changed",
    "masstagging_media_invalid",
    "masstagging_media_too_large",
    "masstagging_import_invalid",
    "masstagging_base_not_found",
]

_MAX_NAME = 120
MAX_TARGETS = 5000
_MAX_TARGET_CHARS = 200
_MAX_ACCOUNTS = 500
_MAX_MEDIA = 10
# Telegram's caption ceiling for a Premium account; a free one gets 200.
MAX_CAPTION = 2048
_MAX_LINK = 512
_MAX_LINK_TEXT = 40
# A media id is ``<sha256>.<ext>`` from the upload endpoint, nothing else.
MEDIA_ID_PATTERN = r"^[0-9a-f]{64}\.(jpg|jpeg|png|webp|mp4|mov)$"


class MasstaggingMedia(BaseModel):
    """An uploaded photo or video; stories take them in turn."""

    model_config = ConfigDict(extra="forbid")

    media_id: str = Field(pattern=MEDIA_ID_PATTERN)
    name: str = Field(min_length=1, max_length=200)


class MasstaggingPace(BaseModel):
    """«Темп» — the only settings a running campaign takes (the board's gear)."""

    model_config = ConfigDict(extra="forbid")

    mentions_per_story: int = Field(default=5, ge=1, le=10)
    stories_per_day: int = Field(default=15, ge=1, le=100)
    story_gap_minutes: MinutesRange = Field(default_factory=lambda: MinutesRange(min=1, max=2))
    dialogs_per_day: int = Field(default=6, ge=1, le=200)
    dialog_gap_seconds: SecondsRange = Field(default_factory=lambda: SecondsRange(min=20, max=60))
    ramp_up: bool = True
    skip_tagged: bool = True
    stop_mode: MasstaggingStopMode = "count"
    # Per account per Start/Continue; 0 = until the list ends.
    stop_tags: int = Field(default=50, ge=0, le=100_000)
    stop_minutes: int = Field(default=0, ge=0, le=100_000)
    flood_pause_seconds: int = Field(default=500, ge=60, le=86_400)
    floods_to_rest: int = Field(default=3, ge=1, le=20)


class MasstaggingSettings(MasstaggingPace):
    """Everything the settings dialog edits, persisted as one JSON document.

    The accounts roster travels beside it (``MasstaggingSettingsUpdate.account_ids``)
    because it is a table, not a field.
    """

    mode: MasstaggingMode = "stories"
    target_source: MasstaggingTargetSource = "manual"
    targets: list[str] = Field(default_factory=list, max_length=MAX_TARGETS)
    # The parser base the list was copied from, shown beside the list; never linked.
    base_name: str = Field(default="", max_length=_MAX_NAME)
    media: list[MasstaggingMedia] = Field(default_factory=list, max_length=_MAX_MEDIA)
    caption: str = Field(default="", max_length=MAX_CAPTION)
    link: str = Field(default="", max_length=_MAX_LINK)
    link_text: str = Field(default="", max_length=_MAX_LINK_TEXT)
    visibility: MasstaggingVisibility = "all"
    lifetime_hours: StoryLifetimeHours = 48
    prime_first: bool = True

    @field_validator("targets")
    @classmethod
    def _bounded_targets(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if any(len(item) > _MAX_TARGET_CHARS for item in cleaned):
            msg = "target too long"
            raise ValueError(msg)
        return cleaned

    @field_validator("link")
    @classmethod
    def _http_link(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned and not cleaned.lower().startswith(("https://", "http://", "tg://")):
            msg = "link must be an http(s) or tg:// address"
            raise ValueError(msg)
        return cleaned


class MasstaggingVersionRequest(BaseModel):
    """The campaign version the operator actually viewed (optimistic lock)."""

    model_config = ConfigDict(extra="forbid")

    expected_updated_at: str = Field(min_length=1, max_length=40)

    @field_validator("expected_updated_at")
    @classmethod
    def _valid_stamp(cls, value: str) -> str:
        try:
            stamp = datetime.fromisoformat(value)
        except ValueError as exc:
            msg = "expected_updated_at must be an ISO datetime"
            raise ValueError(msg) from exc
        if stamp.utcoffset() is None:
            msg = "expected_updated_at must include a timezone"
            raise ValueError(msg)
        return value


class MasstaggingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=_MAX_NAME)


class MasstaggingSettingsUpdate(MasstaggingVersionRequest):
    """Whole-dialog save: name, roster and settings in one write."""

    name: str = Field(min_length=1, max_length=_MAX_NAME)
    account_ids: list[str] = Field(default_factory=list, max_length=_MAX_ACCOUNTS)
    settings: MasstaggingSettings


class MasstaggingCampaign(BaseModel):
    """One campaign as the sidebar lists it."""

    campaign_id: str
    name: str
    mode: MasstaggingMode
    status: MasstaggingStatus
    target_count: int
    account_count: int
    # Why a campaign cannot start yet; empty once it can.
    missing: list[MasstaggingMissing]
    rest_until: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: str


class MasstaggingCampaigns(BaseModel):
    items: list[MasstaggingCampaign]


class MasstaggingSettingsRead(BaseModel):
    """What the settings dialog opens with; ``updated_at`` is the lock token."""

    campaign_id: str
    name: str
    status: MasstaggingStatus
    updated_at: str
    account_ids: list[str]
    settings: MasstaggingSettings


class MasstaggingMediaRead(BaseModel):
    media_id: str
    name: str


class MasstaggingTargetAction(BaseModel):
    """A manual action on one target of the board."""

    model_config = ConfigDict(extra="forbid")

    target_key: str = Field(min_length=1, max_length=_MAX_TARGET_CHARS)
    action: Literal["now", "skip", "requeue"]


class MasstaggingBaseRequest(BaseModel):
    """Copy a saved parser base into the list."""

    model_config = ConfigDict(extra="forbid")

    base_id: str = Field(min_length=1, max_length=64)


class MasstaggingTargetList(BaseModel):
    """Targets read from a file or a parser base, for the dialog to add to its list.

    ``skipped`` counts the lines or people that cannot be a target (no username,
    a bot, a line that is not a username, phone or link).
    """

    targets: list[str]
    skipped: int = 0
    name: str = ""
