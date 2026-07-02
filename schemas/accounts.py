from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Pydantic resolves these annotations at runtime to build the model fields,
# so they cannot live in a TYPE_CHECKING block.
from schemas.spam_status import SpamStatusKind  # noqa: TC001
from schemas.telegram_profile_snapshot import (  # noqa: TC001
    TelegramMusicItem,
    TelegramProfilePhoto,
    TelegramStoryThumb,
)
from schemas.trust import TrustBand  # noqa: TC001

# account_id is later joined into dialogue pair_keys via "|". Restricting the
# charset here is cheaper than escaping every join site downstream. Allows
# digit-only Telegram user_ids and the session-name stems we actually use.
_ACCOUNT_ID_PATTERN = r"^[A-Za-z0-9._-]+$"

AccountStatus = Literal[
    "new",
    "alive",
    "unauthorized",
    "session_error",
    "account_error",
    "flood_wait",
    "network_error",
    "proxy_error",
    "unknown_error",
]


class AccountCreate(BaseModel):
    account_id: str = Field(min_length=1, pattern=_ACCOUNT_ID_PATTERN)
    label: str | None = Field(default=None, min_length=1)
    session_name: str | None = Field(default=None, min_length=1)


class AccountSessionFileImport(BaseModel):
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)
    label: str | None = Field(default=None, min_length=1)


class AccountRead(BaseModel):
    account_id: str = Field(min_length=1)
    label: str | None = None
    session_name: str | None = None
    status: AccountStatus
    user_id: int | None = None
    phone: str | None = None
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    last_checked_at: str | None = None
    created_at: str
    updated_at: str
    device_platform: str | None = None
    device_model: str | None = None
    device_system_version: str | None = None
    device_app_version: str | None = None
    # System language from the device fingerprint, enriched by the service layer
    # for the edit card's read-only device panel (not an ``accounts`` column).
    device_lang: str | None = None
    bio: str | None = None
    proxy_id: str | None = None
    proxy_type: str | None = None
    proxy_host: str | None = None
    proxy_port: int | None = None
    proxy_status: str | None = None
    proxy_last_checked_at: str | None = None
    proxy_last_error: str | None = None
    proxy_exit_ip: str | None = None
    proxy_country_code: str | None = None
    proxy_country_name: str | None = None
    # Health signals enriched by the service layer (services.accounts._table),
    # not the repository — left None when no page-level enrichment ran.
    trust_score: int | None = Field(default=None, ge=0, le=100)
    trust_band: TrustBand | None = None
    spam_status: SpamStatusKind | None = None
    spam_detail: str | None = None


class AccountList(BaseModel):
    accounts: list[AccountRead]


class AccountFilter(BaseModel):
    query: str = ""
    status: AccountStatus | Literal["all"] = "all"
    # Optional pagination. ``limit=None`` returns every match (legacy default).
    limit: int | None = Field(default=None, ge=1)
    offset: int = Field(default=0, ge=0)


class AccountCheckRequest(BaseModel):
    account_id: str = Field(min_length=1, pattern=_ACCOUNT_ID_PATTERN)


class AccountProfileUpdateRequest(BaseModel):
    account_id: str = Field(min_length=1, pattern=_ACCOUNT_ID_PATTERN)
    first_name: str = Field(min_length=1)
    last_name: str | None = None
    username: str | None = None
    bio: str | None = None


class AccountSummary(BaseModel):
    total: int
    alive: int
    permanent_issue: int
    temporary_issue: int
    never_checked: int


class AccountStats(BaseModel):
    """Fleet-wide status roll-up for the Accounts page stat tiles.

    Counts span the whole ``accounts`` table (a single grouped SQL query), not
    the currently-loaded page, so the tiles stay correct across pagination. The
    buckets mirror the design's status vocabulary (``accountDesignStatus``):

    - ``active``    — ``alive``.
    - ``idle``      — ``flood_wait`` (spam-limited, the "idle" tile).
    - ``needs_code`` — ``unauthorized`` / ``new`` (re-auth by login code).
    - ``problem``   — every other non-alive status (banned / session / errors).
    """

    total: int = Field(default=0, ge=0)
    active: int = Field(default=0, ge=0)
    idle: int = Field(default=0, ge=0)
    needs_code: int = Field(default=0, ge=0)
    problem: int = Field(default=0, ge=0)


AccountHealth = Literal["ok", "warn", "fail"]

_PERMANENT_STATUSES: frozenset[AccountStatus] = frozenset(
    {"unauthorized", "session_error", "account_error"},
)


def health_for_status(status: AccountStatus) -> AccountHealth:
    """Map an ``AccountStatus`` to a coarse traffic-light health value.

    - ``ok`` — alive (green).
    - ``fail`` — permanent: unauthorized, session_error, account_error (red).
    - ``warn`` — everything else: new + temporary issues (amber).
    """
    if status == "alive":
        return "ok"
    if status in _PERMANENT_STATUSES:
        return "fail"
    return "warn"


class AccountTableRow(BaseModel):
    account_id: str
    label: str
    status: str
    health: AccountHealth
    telegram: str
    session: str
    device: str
    proxy: str
    last_checked: str
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    bio: str | None = None
    proxy_type: str | None = None
    proxy_host: str | None = None
    proxy_port: int | None = None
    proxy_status: str | None = None
    proxy_last_checked_at: str | None = None
    proxy_last_error: str | None = None
    proxy_exit_ip: str | None = None
    proxy_country_code: str | None = None
    proxy_country_name: str | None = None


class AccountsTableState(BaseModel):
    rows: list[AccountTableRow]
    summary: AccountSummary


class AccountProfileSnapshot(BaseModel):
    """Combined live-profile view rendered by the edit-profile dialog.

    Aggregates the four read-actions (profile, pinned stories, profile music,
    profile-photo history) plus dialog-only UI state (``fetched_at_unix``,
    ``music_supported``, ``error``). ``error`` is set when Telegram refused
    the live fetch — the dialog falls back to whatever fields are still
    populated.
    """

    account_id: str = Field(min_length=1)
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    phone: str | None = None
    bio: str | None = None
    avatar_bytes: bytes | None = None
    stories: list[TelegramStoryThumb] = Field(default_factory=list)
    music: list[TelegramMusicItem] = Field(default_factory=list)
    photos: list[TelegramProfilePhoto] = Field(default_factory=list)
    music_supported: bool = True
    fetched_at_unix: float = 0.0
    error: str | None = None
