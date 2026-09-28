"""Neurocomment *settings* schemas — split from ``schemas.neurocomment`` for the file-size budget.

The second extraction out of that module, and the one its docstring already named as
next. Same rules as ``schemas._neurocomment_requests``: data contract only, no behaviour
(non-negotiable #2), self-contained (pydantic + stdlib only, so ``schemas.neurocomment``
imports these back without a cycle), and re-exported there so
``from schemas.neurocomment import NeurocommentSettings`` keeps working unchanged.

A pure module move: OpenAPI component names are the CLASS names, so the generated
frontend client is unaffected — ``frontend/openapi.json`` still carries
``NeurocommentSettings`` / ``NeurocommentSettingsUpdate`` under exactly those names.

The pair groups cleanly: one is the stored row the engine reads at selection, the other
the operator's edit of it from the Settings screen. Neither shares vocabulary with the
campaign/board read models left behind.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Which message in a channel the fleet answers:
# - ``first`` — the post itself, the moment it lands (the only behaviour there has ever
#   been, hence the default everywhere: nothing changes until an operator flips it).
# - ``reply`` — a HUMAN's comment under the post, so the fleet arrives after the
#   discussion has started instead of opening it.
CommentMode = Literal["first", "reply"]


class NeurocommentSettings(BaseModel):
    """Operator-editable neurocomment limits — the engine reads these at selection."""

    max_comments_per_hour: int = Field(ge=1)
    max_comments_per_channel_per_day: int = Field(ge=0)
    reply_delay_min_seconds: float = Field(ge=0)
    reply_delay_max_seconds: float = Field(ge=0)
    min_trust_score: int = Field(ge=0, le=100)
    comment_mode: CommentMode = "first"
    # How long ``reply`` mode waits for a human comment to appear under a fresh post
    # before it gives that post up. Bounded at two hours: past that the post is off
    # the channel's first screen and a reply reads as necromancy, not conversation.
    reply_wait_minutes: int = Field(default=10, ge=1, le=120)
    updated_at: str = Field(min_length=1)


class NeurocommentSettingsUpdate(BaseModel):
    """Caller-supplied neurocomment-settings change from the Settings screen."""

    model_config = ConfigDict(extra="forbid")

    # Every field is patch-shaped: omitted (``None``) means "leave as stored". Two
    # callers share this one body and neither knows the other's half — the Settings
    # screen's limits form never sends the mode, and the neurocomment page's mode
    # toggle has no limits form, so sending back its cached numbers would roll back
    # a limits save made from another tab since that cache was read.
    max_comments_per_hour: int | None = Field(default=None, ge=1)
    max_comments_per_channel_per_day: int | None = Field(default=None, ge=0)
    reply_delay_min_seconds: float | None = Field(default=None, ge=0)
    # No upper bound, deliberately — ``_generate._sleep_beating``'s docstring says why.
    reply_delay_max_seconds: float | None = Field(default=None, ge=0)
    min_trust_score: int | None = Field(default=None, ge=0, le=100)
    comment_mode: CommentMode | None = None
    reply_wait_minutes: int | None = Field(default=None, ge=1, le=120)

    @model_validator(mode="after")
    def _check_delay_bounds(self) -> NeurocommentSettingsUpdate:
        # The pair travels together, so the bound is checked here on the wire rather
        # than against a stored half the caller never saw.
        low, high = self.reply_delay_min_seconds, self.reply_delay_max_seconds
        if (low is None) != (high is None):
            msg = "reply_delay_min_seconds and reply_delay_max_seconds are sent together"
            raise ValueError(msg)
        if low is not None and high is not None and low > high:
            msg = "reply_delay_min_seconds must not exceed reply_delay_max_seconds"
            raise ValueError(msg)
        return self
