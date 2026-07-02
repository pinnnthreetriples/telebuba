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


class DialoguePartnersResult(BaseModel):
    """Partners paired with one account — see :func:`services.dialogues.get_partners`."""

    partners: list[str] = Field(default_factory=list)


class DialoguePairsResult(BaseModel):
    """Outcome of one :func:`services.dialogues.assign_pairs` call.

    The wrapper keeps the service boundary Pydantic-only (#2) and leaves room
    for per-assignment metadata (e.g. ``reshuffled: bool``) without breaking
    every call site.
    """

    pairs: list[DialoguePair] = Field(default_factory=list)
