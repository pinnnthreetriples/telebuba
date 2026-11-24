"""One read of an account's cloud-password settings, as the security watch stores it."""

from __future__ import annotations

from pydantic import BaseModel


class SecuritySnapshot(BaseModel):
    """Flags and the reset date only; the e-mail pattern is reduced to whether one is pending."""

    has_password: bool
    has_recovery: bool
    pending_reset_date: str | None = None
    email_unconfirmed: bool
    checked_at: str | None = None
