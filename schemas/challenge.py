"""Pydantic models for the neurocomment challenge solver (Ф2 #120).

Pure types, no behaviour. ``BotChallengeMessage`` is what the gateway's
``WaitForBotChallenge`` read-action surfaces to the service layer; the Gemini
``ChallengeDecision`` contract lands with the solver slice (#146).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChallengeInsert(BaseModel):
    """Fields for one new challenge audit row — the boundary into ``insert_challenge``."""

    challenge_hash: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    raw_text: str
    button_labels: list[str] = Field(default_factory=list)
    outcome: str = Field(min_length=1)
    decision_json: str | None = None


class ChallengeRow(BaseModel):
    """One persisted challenge audit row, as the operator drill-down reads it."""

    account_id: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    raw_text: str
    button_labels: list[str] = Field(default_factory=list)
    outcome: str = Field(min_length=1)
    decided_at: str = Field(min_length=1)


class ChallengeRowList(BaseModel):
    """Wrapper so the repo returns a model, never a raw list (non-negotiable #2)."""

    rows: list[ChallengeRow] = Field(default_factory=list)


class ChallengedChannels(BaseModel):
    """Channels that carry a non-solved challenge row — the board's bot_challenge signal."""

    channels: list[str] = Field(default_factory=list)


class BotChallengeMessage(BaseModel):
    """A guardian-bot inline-button challenge addressed to our account.

    ``button_labels`` is the flat, row-order list of inline-button captions;
    ``has_photo`` flags an image challenge (handled as ``give_up`` until vision
    lands in Phase 2).
    """

    text: str = ""
    button_labels: list[str] = Field(default_factory=list)
    message_id: int
    has_photo: bool = False
