"""Ban-check schemas — split from ``schemas.neurocomment`` for the file-size budget.

Data contract only, no behaviour (non-negotiable #2). Used by the "Проверить
каналы" live probe (``services.neurocomment.bans`` → ``api.v1.neurocomment``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChannelBanCheck(BaseModel):
    """Live ban-probe verdict for one campaign channel.

    ``ok`` = at least one serving account can still comment; ``banned`` = every
    serving account is banned/kicked; ``unknown`` = couldn't determine (no serving
    account, comments disabled, or a probe error).
    """

    channel: str = Field(min_length=1)
    status: Literal["ok", "banned", "unknown"]


class ChannelBanCheckList(BaseModel):
    items: list[ChannelBanCheck] = Field(default_factory=list)
