"""Account-privacy Telegram actions — the ``account.getPrivacy`` / ``setPrivacy`` pair.

Sibling of ``telegram_actions_channels.py`` / ``telegram_actions_discovery.py``
(file-size cap); the discriminated unions in ``schemas.telegram_actions`` import
these names back, so callers keep importing every action from
``schemas.telegram_actions`` unchanged.

Why this cluster exists: an avatar and a bio uploaded by the dashboard land on
Telegram correctly, yet strangers still see a letter placeholder and no bio —
the account's own privacy keys (Profile photo / Bio) are restricted to contacts.
These two actions read and set the three keys involved.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator

# What a READ can report. Telegram's rule vector is richer than three levels
# (per-user exceptions, close friends, premium-only, ...), so a base rule we do
# not model collapses to ``unknown`` rather than being guessed at.
PrivacyLevel = Literal["everybody", "contacts", "nobody", "unknown"]
# What a WRITE may request — ``unknown`` is a read-only outcome, never a target.
PrivacyTarget = Literal["everybody", "contacts", "nobody"]


class GetPrivacySettings(BaseModel):
    """Read-only: the account's Profile photo / Bio / Last seen privacy levels."""

    action_type: Literal["get_privacy_settings"] = "get_privacy_settings"


class PrivacySettingsResult(BaseModel):
    """Gateway output for ``GetPrivacySettings`` — one level per privacy key."""

    profile_photo: PrivacyLevel = "unknown"
    bio: PrivacyLevel = "unknown"
    last_seen: PrivacyLevel = "unknown"


class SetPrivacySettings(BaseModel):
    """Set privacy levels. Field contract: ``None`` leaves that key unchanged.

    Mirrors ``UpdateProfile``'s contract: only non-``None`` fields are sent, one
    ``account.setPrivacy`` per field. An all-``None`` action would change
    nothing, so it is refused here instead of reaching Telegram.
    """

    action_type: Literal["set_privacy_settings"] = "set_privacy_settings"
    profile_photo: PrivacyTarget | None = None
    bio: PrivacyTarget | None = None
    last_seen: PrivacyTarget | None = None

    @model_validator(mode="after")
    def _check_any_field(self) -> SetPrivacySettings:
        if self.profile_photo is None and self.bio is None and self.last_seen is None:
            msg = "at least one of profile_photo/bio/last_seen must be set"
            raise ValueError(msg)
        return self
