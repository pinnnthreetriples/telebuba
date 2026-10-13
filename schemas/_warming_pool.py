"""The channel-pool search and the operator's approval of its proposals (item 4.5).

Split from ``schemas.warming`` for the file-size budget. Data contract only.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

_Topic = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=40)]

# ``no_search_account``: no account outside warming may search right now.
PoolFillStatus = Literal["ok", "no_search_account"]


class PoolFillRequest(BaseModel):
    """«Найти каналы»: what to look for, in which language and country."""

    language: str = Field(pattern=r"^[a-z]{2}$")
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    topics: list[_Topic] = Field(min_length=1, max_length=5)


class PoolFillResult(BaseModel):
    """How a search went: hits seen, channels checked, proposals added for approval."""

    status: PoolFillStatus
    found: int = Field(default=0, ge=0)
    checked: int = Field(default=0, ge=0)
    proposed: int = Field(default=0, ge=0)


class ChannelBatchRequest(BaseModel):
    """Body of the approve/reject calls: the proposals the operator ticked."""

    channels: list[str] = Field(min_length=1, max_length=200)
