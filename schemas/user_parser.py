"""User parser — what the operator asks for: mode, sources, accounts, limits, filters.

The fields are the prototype modal's ``ParserForm``. ``UserParserSettings`` is the form
as a preset stores it (it may be half filled in); ``UserParserRequest`` is the same form
the moment a run is started, so it must name at least one source and one account.

Bounds are literals, not config reads: ``schemas/`` may not import ``core``. The fleet
ceilings (how many sources, how many accounts) are the service's, read from config.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ParserMode = Literal["members", "messages", "comments"]
# What the operator asks for. Telegram's buckets are finer (``online``, ``long``,
# ``hidden``); ``any`` is the only choice that lets ``hidden`` through.
LastSeenWant = Literal["any", "recently", "week", "month"]

SOURCE_MAX_LENGTH = 200
WORD_MAX_LENGTH = 64
MAX_WORDS = 200
MAX_SOURCES = 200
MAX_ACCOUNTS = 50
NAME_MAX_LENGTH = 120

Source = Annotated[str, Field(min_length=1, max_length=SOURCE_MAX_LENGTH)]
Word = Annotated[str, Field(min_length=1, max_length=WORD_MAX_LENGTH)]
AccountId = Annotated[str, Field(min_length=1, max_length=64)]
# A preset's or a base's name: trimmed, never blank.
Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX_LENGTH)
]


class UserParserLimits(BaseModel):
    """How much to read, per source. Only the current mode's fields are used."""

    model_config = ConfigDict(extra="forbid")

    # Members per group. ~10 000 is what Telegram is observed to hand out, not a promise.
    members: int = Field(default=5000, ge=1, le=10000)
    # Messages read per chat, and how many days back.
    messages: int = Field(default=1000, ge=1, le=50000)
    days: int = Field(default=30, ge=1, le=365)
    # Latest posts per channel, comments read per post, shortest comment that counts.
    posts: int = Field(default=50, ge=1, le=1000)
    per_post: int = Field(default=200, ge=1, le=5000)
    min_length: int = Field(default=0, ge=0, le=500)


class UserParserToggles(BaseModel):
    """The form's switches, one per filter. Defaults are the prototype's."""

    model_config = ConfigDict(extra="forbid")

    skip_bots: bool = True
    skip_deleted: bool = True
    skip_scam: bool = True
    with_username: bool = False
    with_photo: bool = False
    premium_only: bool = False
    with_stories: bool = False
    include_replies: bool = True
    include_forwards: bool = False
    exclude_own: bool = True
    exclude_admins: bool = True
    exclude_collected: bool = False


class UserParserSettings(BaseModel):
    """The whole form. A preset stores this; a run is started with the stricter request."""

    model_config = ConfigDict(extra="forbid")

    mode: ParserMode = "comments"
    sources: list[Source] = Field(default_factory=list, max_length=MAX_SOURCES)
    keywords: list[Word] = Field(default_factory=list, max_length=MAX_WORDS)
    account_ids: list[AccountId] = Field(default_factory=list, max_length=MAX_ACCOUNTS)
    limits: UserParserLimits = Field(default_factory=UserParserLimits)
    # How many times a person must have written (messages and comments modes).
    min_messages: int = Field(default=1, ge=1, le=100)
    # In how many of the listed sources a person must have been found.
    min_sources: int = Field(default=1, ge=1, le=MAX_SOURCES)
    last_seen: LastSeenWant = "any"
    stop_words: list[Word] = Field(default_factory=list, max_length=MAX_WORDS)
    blacklist: list[Word] = Field(default_factory=list, max_length=MAX_WORDS)
    toggles: UserParserToggles = Field(default_factory=UserParserToggles)
    protect: bool = True
    fast: bool = False
    # Seconds between two sources, and between two reads of one account.
    chat_delay: float = Field(default=5, ge=1, le=120)
    request_delay: float = Field(default=1, ge=0.5, le=30)


class UserParserRequest(UserParserSettings):
    """The form as a run is started with it: something to read, someone to read with."""

    sources: list[Source] = Field(min_length=1, max_length=MAX_SOURCES)
    account_ids: list[AccountId] = Field(min_length=1, max_length=MAX_ACCOUNTS)
    # The base's name as the modal words it in the operator's language; renamable later.
    name: str | None = Field(default=None, max_length=NAME_MAX_LENGTH)

    @model_validator(mode="after")
    def _distinct_accounts(self) -> UserParserRequest:
        self.account_ids = list(dict.fromkeys(self.account_ids))
        return self


UserParserStartStatus = Literal[
    "started", "already_running", "account_busy", "account_cooling", "no_account"
]


class UserParserStartOutcome(BaseModel):
    """A start's answer. A refusal names the first account it tripped over."""

    status: UserParserStartStatus
    run_id: str | None = None
    refused_account_id: str | None = None


# Why an account cannot read right now; the SPA owns the wording.
UserParserBusyReason = Literal["no_session", "account_busy", "account_cooling"]


class UserParserAccountOption(BaseModel):
    account_id: str
    name: str
    username: str | None = None
    premium: bool | None = None
    busy_reason: UserParserBusyReason | None = None


class UserParserAccountList(BaseModel):
    items: list[UserParserAccountOption]


class UserParserPresetWrite(BaseModel):
    name: Name
    settings: UserParserSettings


class UserParserPreset(BaseModel):
    preset_id: str
    name: str
    settings: UserParserSettings
    created_at: str


class UserParserPresetList(BaseModel):
    items: list[UserParserPreset]


# 409 ``message`` when another preset already has the name (case-insensitive).
PRESET_NAME_TAKEN = "user_parser_preset_name_taken"
# 409 ``message`` when a base is deleted while its run is still collecting.
RUN_STILL_RUNNING = "user_parser_run_running"
