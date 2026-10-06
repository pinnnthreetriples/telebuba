"""Pydantic contracts for the chat-broadcast domain («Рассылка по чатам»).

Selected accounts write a chain of messages into Telegram groups — from a list (joining
when they are not inside) or into the groups they are already in. This module carries
the campaign, its settings and the requests; the board lives in
``schemas.chat_broadcast_board``. No behaviour, no I/O.

``ChatBroadcastRefusalCode`` is declared whole so ``tests/test_error_code_i18n_parity.py``
can hold the SPA's ``shell.code.*`` table to it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ChatBroadcastStatus = Literal[
    "draft", "running", "stopping", "stopped", "done", "failed", "stalled"
]
ChatBroadcastTargetMode = Literal["list", "own"]
ChatBroadcastFirstMessage = Literal["template", "ai"]
ChatBroadcastMessageKind = Literal["text", "post"]
ChatBroadcastStopMode = Literal["count", "time"]
ApprovalWaitHours = Literal[1, 3, 6, 12, 24]
JoinDelayMinutes = Literal[0, 30, 60, 120]

ChatBroadcastRefusalCode = Literal[
    "campaign_not_found",
    "campaign_running",
    "campaign_changed",
    "campaign_not_resumable",
    "no_accounts",
    "no_free_accounts",
    "no_targets",
    "no_messages",
    "invalid_target",
    "invalid_post_link",
    "too_many_targets",
    "too_many_accounts",
    "account_not_found",
    "target_not_found",
    "target_state_changed",
    "account_not_in_campaign",
    "media_invalid",
    "media_too_large",
    "media_not_found",
]

_MAX_NAME = 120
_MAX_TEXT = 4096
_MAX_POST_LINK = 200
_MAX_BRIEF = 2000
_MAX_TARGETS = 2000
_MAX_TARGET_CHARS = 300
_MAX_EXCLUDED = 5000
_MAX_MESSAGES = 20
_MAX_ACCOUNTS = 500
# A media id is ``<sha256>.<ext>`` from the upload endpoint, nothing else.
MEDIA_ID_PATTERN = r"^[0-9a-f]{64}\.(jpg|jpeg|png|webp)$"


class _Range(BaseModel):
    """A ``min``-``max`` pair, inclusive; a random value is drawn from it every time."""

    model_config = ConfigDict(extra="forbid")

    min: int = Field(ge=0)
    max: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> _Range:
        if self.min > self.max:
            msg = "min must not exceed max"
            raise ValueError(msg)
        return self


class SecondsRange(_Range):
    min: int = Field(ge=0, le=3600)
    max: int = Field(ge=0, le=3600)


class MinutesRange(_Range):
    min: int = Field(ge=0, le=1440)
    max: int = Field(ge=0, le=1440)


class ChatBroadcastPhoto(BaseModel):
    """An uploaded photo attached to a text message; the text becomes its caption."""

    model_config = ConfigDict(extra="forbid")

    media_id: str = Field(pattern=MEDIA_ID_PATTERN)
    name: str = Field(min_length=1, max_length=200)


class ChatBroadcastMessage(BaseModel):
    """One message of the chain: text (optionally with a photo) or a forwarded post."""

    model_config = ConfigDict(extra="forbid")

    kind: ChatBroadcastMessageKind = "text"
    text: str = Field(default="", max_length=_MAX_TEXT)
    photo: ChatBroadcastPhoto | None = None
    # ``t.me/<channel>/<id>`` (or ``t.me/c/<id>/<post>``) of a post to forward whole.
    post: str = Field(default="", max_length=_MAX_POST_LINK)


class ChatBroadcastSettings(BaseModel):
    """Everything the settings dialog edits, persisted as one JSON document.

    Defaults are the ones agreed on the mockup; the accounts roster travels beside it
    (``ChatBroadcastSettingsUpdate.account_ids``) because it is a table, not a field.
    """

    model_config = ConfigDict(extra="forbid")

    target_mode: ChatBroadcastTargetMode = "list"
    targets: list[str] = Field(default_factory=list, max_length=_MAX_TARGETS)
    # Groups of "where it already is" the operator crossed out, as peer ids.
    own_excluded: list[str] = Field(default_factory=list, max_length=_MAX_EXCLUDED)
    first_message: ChatBroadcastFirstMessage = "template"
    ai_brief: str = Field(default="", max_length=_MAX_BRIEF)
    randomize: bool = True
    messages: list[ChatBroadcastMessage] = Field(default_factory=list, max_length=_MAX_MESSAGES)
    approval_wait_hours: ApprovalWaitHours = 24
    join_delay_minutes: JoinDelayMinutes = 60
    between_chats: SecondsRange = Field(default_factory=lambda: SecondsRange(min=30, max=90))
    between_messages: SecondsRange = Field(default_factory=lambda: SecondsRange(min=3, max=8))
    typing: bool = True
    stop_mode: ChatBroadcastStopMode = "count"
    stop_messages: int = Field(default=100, ge=1, le=1_000_000)
    stop_hours: int = Field(default=6, ge=1, le=24 * 365)
    loop: bool = True
    rest_minutes: MinutesRange = Field(default_factory=lambda: MinutesRange(min=60, max=120))
    # 0 = until stopped.
    rounds: int = Field(default=3, ge=0, le=10_000)
    skip_already_written: bool = False
    account_limit: bool = True
    per_hour: int = Field(default=20, ge=1, le=10_000)
    per_day: int = Field(default=120, ge=1, le=100_000)
    skip_errors: bool = True
    skip_deleted: bool = True
    # 0 = never stop an account for its errors.
    max_consecutive_errors: int = Field(default=10, ge=0, le=10_000)

    @field_validator("targets")
    @classmethod
    def _bounded_targets(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if any(len(item) > _MAX_TARGET_CHARS for item in cleaned):
            msg = "target too long"
            raise ValueError(msg)
        return cleaned


class ChatBroadcastVersionRequest(BaseModel):
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


class ChatBroadcastCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=_MAX_NAME)


class ChatBroadcastRename(ChatBroadcastVersionRequest):
    name: str = Field(min_length=1, max_length=_MAX_NAME)


class ChatBroadcastSettingsUpdate(ChatBroadcastVersionRequest):
    """Whole-dialog save: name, roster and settings in one write."""

    name: str = Field(min_length=1, max_length=_MAX_NAME)
    account_ids: list[str] = Field(default_factory=list, max_length=_MAX_ACCOUNTS)
    settings: ChatBroadcastSettings


class ChatBroadcastCampaign(BaseModel):
    """One campaign as the sidebar lists it."""

    campaign_id: str
    name: str
    status: ChatBroadcastStatus
    target_mode: ChatBroadcastTargetMode
    account_count: int
    target_count: int
    round: int
    rest_until: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: str


class ChatBroadcastCampaigns(BaseModel):
    items: list[ChatBroadcastCampaign]


class ChatBroadcastSettingsRead(BaseModel):
    """What the settings dialog opens with; ``updated_at`` is the lock token."""

    campaign_id: str
    name: str
    status: ChatBroadcastStatus
    updated_at: str
    account_ids: list[str]
    settings: ChatBroadcastSettings


class ChatBroadcastMediaRead(BaseModel):
    media_id: str
    name: str


class ChatBroadcastTargetAction(BaseModel):
    """A manual action on one chat of the board."""

    model_config = ConfigDict(extra="forbid")

    chat_key: str = Field(min_length=1, max_length=_MAX_TARGET_CHARS)
    action: Literal["now", "hand", "skip"]
    # Required for ``hand``: the account the chat goes to.
    account_id: str | None = Field(default=None, min_length=1, max_length=64)


class ChatBroadcastResolveRequest(BaseModel):
    """Classify pasted chat links; a folder link is opened with one of the accounts."""

    model_config = ConfigDict(extra="forbid")

    targets: list[str] = Field(max_length=_MAX_TARGETS)
    account_ids: list[str] = Field(default_factory=list, max_length=_MAX_ACCOUNTS)
