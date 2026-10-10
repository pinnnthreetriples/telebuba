"""User parser — a run as the modal follows it, and the people it kept.

Split from ``schemas.user_parser`` for the file-size cap. Every run is also a base («Базы»
in the modal): one saved folder of people, kept until the operator deletes it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from schemas.telegram_actions_user_parser import LastSeenBucket  # noqa: TC001
from schemas.user_parser import Name, ParserMode  # noqa: TC001 - pydantic resolves them

UserParserRunStatus = Literal["running", "done", "stopped", "failed", "interrupted"]
# Per source. ``partial``: Telegram handed out fewer members than it says the group
# has; ``hidden``: the group hides its member list; ``join_failed``: an invite could not
# be joined (limit, join request, ban); ``flood``: every account was rate-limited
# before the source was finished.
UserParserSourceStatus = Literal[
    "pending", "ok", "partial", "hidden", "join_failed", "flood", "failed"
]
UserParserAccountState = Literal["idle", "reading", "waiting", "flooded", "dropped", "done"]


class UserParserSourceReport(BaseModel):
    source: str
    status: UserParserSourceStatus = "pending"
    # Distinct people this source gave, before the filters.
    count: int = 0
    # Members the group says it has, when it says (members mode).
    total: int | None = None
    finished_at: str | None = None


class UserParserAccountProgress(BaseModel):
    account_id: str
    name: str
    state: UserParserAccountState = "idle"
    reads: int = 0
    # Until when a flood parks it, ISO; ``None`` when it is not waiting one out.
    flood_until: str | None = None


class UserParserRun(BaseModel):
    """A run: the row it is stored as, plus the live progress while it is collecting."""

    run_id: str
    name: str
    mode: ParserMode
    status: UserParserRunStatus
    created_at: str
    finished_at: str | None = None
    stop_reason: str | None = None
    sources_total: int = 0
    sources_done: int = 0
    # Distinct people seen, before the filters — beside ``kept`` it says how many went.
    collected_raw: int = 0
    kept: int = 0
    sources: list[UserParserSourceReport] = Field(default_factory=list)
    accounts: list[UserParserAccountProgress] = Field(default_factory=list)
    # Why people were dropped, filter code -> count; filled in once the run settles.
    filtered: dict[str, int] = Field(default_factory=dict)


class UserParserUser(BaseModel):
    """One kept person. Message counts and dates are zero/absent in members mode."""

    user_id: int
    username: str | None = None
    first_name: str = ""
    last_name: str = ""
    is_bot: bool = False
    is_deleted: bool = False
    is_scam: bool = False
    is_fake: bool = False
    is_premium: bool = False
    has_photo: bool = False
    has_stories: bool = False
    last_seen: LastSeenBucket = "hidden"
    message_count: int = 0
    first_at: str | None = None
    last_at: str | None = None
    sources: list[str] = Field(default_factory=list)


class UserParserUserPage(BaseModel):
    items: list[UserParserUser]
    total: int


class UserParserBase(BaseModel):
    """A finished run as a folder in «Базы»."""

    run_id: str
    name: str
    mode: ParserMode
    status: UserParserRunStatus
    created_at: str
    sources: list[str]
    kept: int


class UserParserBaseList(BaseModel):
    items: list[UserParserBase]


class UserParserBaseRename(BaseModel):
    name: Name


ExportFormat = Literal["csv", "json"]
