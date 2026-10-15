"""Board roll-up, channel and warmed-pool models, split from ``schemas.warming`` for size.

Re-exported there, so ``from schemas.warming import WarmingSummary`` keeps working.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from schemas.device_fingerprint import (
    DeviceType,
    desktop_only,
)
from schemas.geo import GeoMatchStatus  # noqa: TC001 - pydantic resolves it at runtime.
from schemas.survival import AccountSurvival  # noqa: TC001 - pydantic resolves it at runtime.


class WarmingSummary(BaseModel):
    """Fleet-level roll-up shown at the top of the warming page."""

    total: int = Field(default=0, ge=0)
    warming: int = Field(default=0, ge=0)
    active: int = Field(default=0, ge=0)
    ready: int = Field(default=0, ge=0)
    attention: int = Field(default=0, ge=0)
    trust_healthy: int = Field(default=0, ge=0)
    trust_watch: int = Field(default=0, ge=0)
    trust_risk: int = Field(default=0, ge=0)


class WarmingMechanics(BaseModel):
    """Optional warming steps and whether the current settings let them run."""

    join: bool = True
    reactions: bool = True
    stories: bool = True
    # Needs the switch AND a text-LLM key: without a key the chat step never writes.
    dialogue: bool = False


class AddChannelsRequest(BaseModel):
    """Raw user input — one or many links/usernames, newline- or comma-separated."""

    model_config = ConfigDict(extra="forbid")

    raw: str = Field(min_length=1)


class RemoveChannelRequest(BaseModel):
    channel: str = Field(min_length=1)


class WarmedAccount(BaseModel):
    """A graduated (operator-promoted) account, for the warming page's warmed card."""

    account_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    warming_days: int = Field(ge=0)
    # Worked days the account graduated with — the "X / Y дней" X (stage 1.7).
    warmed_days: int = Field(default=0, ge=0)
    # Card meta sourced from the warming board card (so the design's warmed card
    # shows the real phone / flag / proxy badge / trust instead of a mock).
    phone: str | None = None
    # Telegram display name parts, so cards/modal show the name like the accounts table.
    first_name: str | None = None
    last_name: str | None = None
    # Cached Telegram avatar etag; the card renders /avatar?v={etag} when set, initials otherwise.
    avatar_etag: str | None = None
    phone_country: str | None = None
    proxy_type: str | None = None
    proxy_country: str | None = None
    trust_score: int | None = Field(default=None, ge=0, le=100)
    # The warming target (days) the account graduated against — the "X / Y дней" Y.
    target_days: int = Field(ge=0)
    # True once handed to the neurocomment idle pool: the warming page hides the
    # account, the neurocomment page starts counting/offering it.
    nc_handed_off: bool = False
    # The geo verdict of the card, so «Вернуть в прогрев» can warn like a start does.
    geo_status: GeoMatchStatus = "unknown"
    geo_accepted: bool = False
    # «День 12 после выпуска · жив» — the card's survival badge.
    survival: AccountSurvival | None = None
    # The account's devices, primary first: what «Вернуть в прогрев» may offer.
    devices: list[DeviceType] = Field(default_factory=desktop_only)
    # The wave the account graduated in and when that wave began (the card names it).
    wave: int = Field(default=1, ge=1)
    started_at: str | None = None
    # A kept spam block («Спамблок» badge until a clean @SpamBot answer lifts it).
    spam_blocked_at: str | None = None


class WarmedAccountList(BaseModel):
    """Wrapper for a bulk read of warmed accounts (non-negotiable #2)."""

    accounts: list[WarmedAccount] = Field(default_factory=list)
