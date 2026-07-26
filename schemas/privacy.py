"""API-facing account-privacy models — what the SPA sends and receives.

``AccountPrivacyView`` follows the ``AccountProfileView`` error-envelope idiom: a
live read Telegram refused comes back as a populated ``error`` with no
``settings``, so the modal still opens instead of failing the request.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

# Pydantic resolves these annotations at class-build time, so they cannot live
# in a TYPE_CHECKING block.
from schemas.telegram_actions_privacy import (  # noqa: TC001
    PrivacySettingsResult,
    PrivacyTarget,
)


class AccountPrivacyView(BaseModel):
    """One account's live privacy levels, or why they could not be read."""

    settings: PrivacySettingsResult | None = None
    error: str | None = None


class AccountPrivacyUpdateRequest(BaseModel):
    """Privacy write body. ``None`` leaves that key unchanged (same as the action).

    ``extra="forbid"`` so a typo'd key 422s instead of silently no-op-ing, and an
    all-``None`` body is refused for the same reason the action refuses it: it
    would spend a Telegram round trip changing nothing.
    """

    model_config = ConfigDict(extra="forbid")

    profile_photo: PrivacyTarget | None = None
    bio: PrivacyTarget | None = None
    last_seen: PrivacyTarget | None = None

    @model_validator(mode="after")
    def _check_any_field(self) -> AccountPrivacyUpdateRequest:
        if self.profile_photo is None and self.bio is None and self.last_seen is None:
            msg = "at least one of profile_photo/bio/last_seen must be set"
            raise ValueError(msg)
        return self


class AccountPrivacyOutcome(BaseModel):
    """One account's result in the fleet-wide apply.

    ``skipped`` = the account is permanently dead, and ``error`` carries the
    account status that caused the skip, so a count is explainable.

    ``failed`` carries whatever ``AccountActionError`` reported. That is USUALLY a
    stable code the SPA translates under ``accounts.profile.code.*``, but not
    always: ``raise_for_result`` falls back to ``ActionResult.error_message`` for
    the generic-failure family, which is a Telethon message. Repo-wide behaviour
    for every account action, not specific to privacy — so the SPA renders an
    unknown value as-is rather than pretending it is translatable. The infra
    family (pool / socket / proxy) is already collapsed to the flat ``unavailable``
    code before it gets here, which is what keeps a proxy endpoint off the wire.
    """

    account_id: str
    status: Literal["ok", "failed", "skipped"]
    error: str | None = None


class BulkPrivacyResult(BaseModel):
    """Fleet-wide apply roll-up — per-account outcomes plus the three counts."""

    outcomes: list[AccountPrivacyOutcome]
    ok: int
    failed: int
    skipped: int
