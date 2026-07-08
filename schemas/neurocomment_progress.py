"""Onboarding-progress schemas — split from ``schemas.neurocomment`` (file-size cap).

One-way import of ``OnboardingState`` from ``schemas.neurocomment`` (no cycle: that
module must not import this one).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from schemas.neurocomment import (
    OnboardingState,  # noqa: TC001 - Pydantic needs the runtime type for field schema.
)

OnboardingProgressCode = Literal[
    "onboarding_started",
    "spam_probe_started",
    "spam_probe_failed",
    "channel_resolving",
    "channel_resolved",
    "channel_resolve_failed",
    "channel_comments_off",
    "channel_all_ready",
    "pair_already_ready",
    "pair_join_delay",
    "pair_joining",
    "pair_result",
    "onboarding_finished",
]


class OnboardingProgressEvent(BaseModel):
    """One structured onboarding-progress step (locale-neutral, #12).

    Replaces the pre-translated Russian progress strings: the service emits a code
    plus the relevant fields, and the SPA owns all i18n. Every field bar ``code`` is
    optional — each code populates only the fields it needs.
    """

    code: OnboardingProgressCode
    account_id: str | None = None
    channel: str | None = None
    account_count: int | None = Field(default=None, ge=0)
    channel_count: int | None = Field(default=None, ge=0)
    ready_count: int | None = Field(default=None, ge=0)
    total_count: int | None = Field(default=None, ge=0)
    delay_seconds: float | None = Field(default=None, ge=0)
    state: OnboardingState | None = None
    reason: str | None = None
