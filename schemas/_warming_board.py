"""Board roll-up models — split from ``schemas.warming`` for the file-size budget.

Re-exported there, so ``from schemas.warming import WarmingSummary`` keeps working.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


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
