"""Pydantic models for the neurocomment challenge solver (Ф2 #120).

Pure types, no behaviour. ``BotChallengeMessage`` is what the gateway's
``WaitForBotChallenge`` read-action surfaces to the service layer; the Gemini
``ChallengeDecision`` contract lands with the solver slice (#146).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


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
    # Gemini's "why this action" (#148 drill-down); None for pre-Gemini give_ups.
    reasoning: str | None = None


class ChallengeRowList(BaseModel):
    """Wrapper so the repo returns a model, never a raw list (non-negotiable #2)."""

    rows: list[ChallengeRow] = Field(default_factory=list)


class ChallengeOutcomeCounts(BaseModel):
    """The four header counters over the challenge audit table for a time window (#148)."""

    solved: int = 0
    failed: int = 0
    give_up: int = 0
    pending: int = 0


class ChallengedChannels(BaseModel):
    """Channels that carry a non-solved challenge row — the board's bot_challenge signal."""

    channels: list[str] = Field(default_factory=list)


class ChallengeDecision(BaseModel):
    """Gemini's structured verdict on a challenge (server-side ``responseSchema``).

    Validators mirror the action contract: ``click_button`` needs ``button_index``;
    ``send_text`` needs ``text``; ``give_up`` carries neither.
    """

    action: Literal["click_button", "send_text", "give_up"]
    button_index: int | None = None
    text: str | None = None
    confidence: float = Field(ge=0, le=1)
    reasoning: str = Field(max_length=200)

    @model_validator(mode="after")
    def _check_action_fields(self) -> ChallengeDecision:
        if self.action == "click_button" and self.button_index is None:
            msg = "click_button requires button_index"
            raise ValueError(msg)
        if self.action == "send_text" and not self.text:
            msg = "send_text requires text"
            raise ValueError(msg)
        if self.action == "give_up" and (self.button_index is not None or self.text is not None):
            msg = "give_up must have button_index and text both None"
            raise ValueError(msg)
        return self


class BotChallengeMessage(BaseModel):
    """A guardian-bot inline-button challenge addressed to our account.

    ``button_labels`` is the flat, row-order list of inline-button captions;
    ``has_photo`` flags an image challenge, which the solver reads with Gemini
    vision. ``image_b64`` carries the downloaded photo bytes (base64) for that
    vision call; it is transient (never persisted in the audit row) and ``None``
    when there is no photo or the download failed.
    """

    text: str = ""
    button_labels: list[str] = Field(default_factory=list)
    message_id: int
    has_photo: bool = False
    image_b64: str | None = None
    image_mime: str = Field(default="image/jpeg", min_length=1)
