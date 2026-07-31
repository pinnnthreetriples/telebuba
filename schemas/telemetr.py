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

# One value per operator-actionable outcome, because the advice differs: a wrong key
# needs a new key, an exhausted quota needs a bigger plan, and an unresolvable filter
# needs a different dropdown value. ``not_configured`` is returned before any socket
# is opened when the operator has not supplied a key — a skipped source, not a
# failure. ``error`` stays the catch-all for transport, 5xx and malformed bodies.
TelemetrStatus = Literal[
    "ok",
    "error",
    "rate_limited",
    "quota_exhausted",
    "subscription_inactive",
    "auth_failed",
    "forbidden",
    "bad_request",
    "not_found",
    "unresolved_filter",
    "not_configured",
]

# Telemetr.io caps ``limit`` on /catalog/search at 100.
TELEMETR_MAX_LIMIT = 100
# Telegram's own channel-title ceiling. Bounding it here stops a third party's
# over-long title from riding through persistence into every SPA board poll; the
# country/language tags a row carries are bounded by the same reasoning.
TELEMETR_MAX_TITLE_LENGTH = 128
# A count above this cannot be persisted (SQLite rejects an out-of-range integer),
# and the write happens after the whole run's candidates are merged — so one absurd
# row would cost every other candidate, native hits included.
TELEMETR_MAX_MEMBERS = 2**31 - 1


class TelemetrSearchRequest(BaseModel):
    # ``repr=False``: the key must not surface in a traceback or a logged model.
    api_key: str = Field(default="", repr=False)
    term: str = Field(min_length=1, max_length=64)
    # ``min_length``: a blank filter is "unset", and the UI already omits it — but a
    # non-UI caller could post one, and a bare ``?country=`` on the wire is a filter
    # the operator never asked for.
    country: str | None = Field(default=None, min_length=1, max_length=32)
    language: str | None = Field(default=None, min_length=1, max_length=32)
    members_min: int | None = Field(default=None, ge=0)
    members_max: int | None = Field(default=None, ge=0)
    limit: int = Field(default=30, ge=1, le=TELEMETR_MAX_LIMIT)


class TelemetrChannel(BaseModel):
    """One catalogue row, narrowed to the fields discovery actually uses.

    ``country``/``language`` are the values Telemetr.io itself filed the channel
    under, so the operator can see and verify what a filter actually returned.
    """

    username: str = Field(min_length=1)
    title: str = Field(default="", max_length=TELEMETR_MAX_TITLE_LENGTH)
    members_count: int | None = Field(default=None, ge=0, le=TELEMETR_MAX_MEMBERS)
    country: str | None = Field(default=None, max_length=TELEMETR_MAX_TITLE_LENGTH)
    language: str | None = Field(default=None, max_length=TELEMETR_MAX_TITLE_LENGTH)


class TelemetrSearchResult(BaseModel):
    """One source's outcome, never an exception.

    ``total_count`` is the catalogue's own match total, so truncation to ``limit`` (and
    to the rows that survived handle resolution) is visible instead of silent.
    """

    status: TelemetrStatus
    items: list[TelemetrChannel] = Field(default_factory=list)
    total_count: int | None = Field(default=None, ge=0, le=TELEMETR_MAX_MEMBERS)
    error: str | None = None
