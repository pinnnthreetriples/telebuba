"""Pydantic contracts for saved chat lists («Категории чатов») of the chat broadcast.

A collection is a named list of chat links the operator keeps for reuse. Picking one in
a campaign's settings COPIES its links into that campaign; nothing links the two
afterwards. No behaviour, no I/O.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_MAX_NAME = 64
# The campaign's own ceiling: a collection must fit into one campaign whole.
_MAX_TARGETS = 2000


class ChatCollection(BaseModel):
    collection_id: str
    name: str
    targets: list[str]
    updated_at: str


class ChatCollections(BaseModel):
    items: list[ChatCollection]


class ChatCollectionWrite(BaseModel):
    """Create, or replace whole: the name and every link at once."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=_MAX_NAME)
    targets: list[str] = Field(default_factory=list, max_length=_MAX_TARGETS)
