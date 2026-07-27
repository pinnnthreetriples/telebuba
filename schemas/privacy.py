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

    ``failed`` carries whatever ``AccountActionError`` reported: always a stable
    code the SPA translates under ``accounts.profile.code.*`` — either a gateway
    code or the bare ``ActionStatus`` (``failed``) when the refusal came from an
    exception whose message is not a contract, never a Telethon message.
    Repo-wide behaviour for every account action, not specific to privacy. The
    infra family (pool / socket / proxy) is already collapsed to the flat
    ``unavailable`` code before it gets here, which is what keeps a proxy
    endpoint off the wire.

    ``applied`` names the keys that DID change on Telegram before the refusal.
    ``account.setPrivacy`` is one call per key and nothing is rolled back, so a
    ``failed`` row can already have published the avatar — reporting it as a bare
    failure inverted the safety-relevant fact for a feature whose entire purpose is
    controlling visibility. Only meaningful on ``failed``: an ``ok`` row applied
    every key the request set and a ``skipped`` row applied none, so both leave it
    empty rather than restating the request.

    ``retry_after_seconds`` is the server-mandated wait that came with a flood-family
    refusal. ``AccountActionError`` has always carried it, but this model dropped it,
    so a ``flood_wait`` row rendered "retry in ? s" — the one fact that makes the
    error actionable. Only the flood family sets it; every other refusal leaves it
    ``None``.
    """

    account_id: str
    status: Literal["ok", "failed", "skipped"]
    error: str | None = None
    applied: list[str] = []
    retry_after_seconds: int | None = None


class BulkPrivacyResult(BaseModel):
    """Fleet-wide apply roll-up — per-account outcomes plus the three counts."""

    outcomes: list[AccountPrivacyOutcome]
    ok: int
    failed: int
    skipped: int
