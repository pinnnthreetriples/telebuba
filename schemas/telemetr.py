"""Pydantic schemas for the Telemetr.io channel-catalogue gateway.

Flow between ``services/neurocomment/_discovery_providers.py`` (which asks for
candidate channels) and ``core/telemetr.py`` (the only module that talks HTTP to
Telemetr.io). No behaviour.

``api_key`` rides on the request because ``schemas/`` may not read ``settings``;
the service resolves it from the settings row and passes it in, exactly like
``GeminiRequest``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ``not_configured`` is returned before any socket is opened when the operator has
# not supplied a key — a skipped source, not a failure.
TelemetrStatus = Literal["ok", "error", "rate_limited", "not_configured"]

# Telemetr.io caps ``limit`` on /catalog/search at 100.
TELEMETR_MAX_LIMIT = 100
# Telegram's own channel-title ceiling. Bounding it here stops a third party's
# over-long title from riding through persistence into every SPA board poll.
TELEMETR_MAX_TITLE_LENGTH = 128
# A count above this cannot be persisted (SQLite rejects an out-of-range integer),
# and the write happens after the whole run's candidates are merged — so one absurd
# row would cost every other candidate, native hits included.
TELEMETR_MAX_MEMBERS = 2**31 - 1


class TelemetrSearchRequest(BaseModel):
    api_key: str = ""
    term: str = Field(min_length=1, max_length=64)
    country: str | None = None
    language: str | None = None
    members_min: int | None = Field(default=None, ge=0)
    members_max: int | None = Field(default=None, ge=0)
    limit: int = Field(default=30, ge=1, le=TELEMETR_MAX_LIMIT)


class TelemetrChannel(BaseModel):
    """One catalogue row, narrowed to the fields discovery actually uses."""

    username: str = Field(min_length=1)
    title: str = Field(default="", max_length=TELEMETR_MAX_TITLE_LENGTH)
    members_count: int | None = Field(default=None, ge=0, le=TELEMETR_MAX_MEMBERS)


class TelemetrSearchResult(BaseModel):
    status: TelemetrStatus
    items: list[TelemetrChannel] = Field(default_factory=list)
    error: str | None = None
