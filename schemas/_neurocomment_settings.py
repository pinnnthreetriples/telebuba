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

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NeurocommentSettings(BaseModel):
    """Operator-editable neurocomment limits — the engine reads these at selection."""

    max_comments_per_hour: int = Field(ge=1)
    max_comments_per_channel_per_day: int = Field(ge=0)
    reply_delay_min_seconds: float = Field(ge=0)
    reply_delay_max_seconds: float = Field(ge=0)
    min_trust_score: int = Field(ge=0, le=100)
    updated_at: str = Field(min_length=1)


class NeurocommentSettingsUpdate(BaseModel):
    """Caller-supplied neurocomment-settings change from the Settings screen."""

    model_config = ConfigDict(extra="forbid")

    max_comments_per_hour: int = Field(ge=1)
    max_comments_per_channel_per_day: int = Field(ge=0)
    reply_delay_min_seconds: float = Field(ge=0)
    # No upper bound, deliberately — ``_generate._sleep_beating``'s docstring says why.
    reply_delay_max_seconds: float = Field(ge=0)
    min_trust_score: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _check_delay_bounds(self) -> NeurocommentSettingsUpdate:
        if self.reply_delay_min_seconds > self.reply_delay_max_seconds:
            msg = "reply_delay_min_seconds must not exceed reply_delay_max_seconds"
            raise ValueError(msg)
        return self
