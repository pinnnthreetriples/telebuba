"""Schemas for inter-account dialogue pairing.

No behaviour — the data contract for "who talks to whom". See non-negotiable #2.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DialoguePair(BaseModel):
    """One undirected acquaintance pair (``account_a`` < ``account_b``)."""

    account_a: str = Field(min_length=1)
    account_b: str = Field(min_length=1)
    assigned_at: str = Field(min_length=1)


class DialogueMessage(BaseModel):
    """One message exchanged between two paired accounts."""

    id: int
    pair_key: str = Field(min_length=1)
    from_account: str = Field(min_length=1)
    to_account: str = Field(min_length=1)
    text: str = Field(min_length=1)
    created_at: str = Field(min_length=1)
    replied: bool = False


class DialogueOverview(BaseModel):
    """Pairs and recent messages for the warming page's dialogue panel."""

    pairs: list[DialoguePair] = Field(default_factory=list)
    recent: list[DialogueMessage] = Field(default_factory=list)
