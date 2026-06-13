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
