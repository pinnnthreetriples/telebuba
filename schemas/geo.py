"""Schemas for proxy/phone geo-consistency checks.

No behaviour — the verdict produced by ``core.phone_geo`` and surfaced by the
accounts UI. See non-negotiable #2.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# match    — proxy country == phone country.
# mismatch — they differ (non-blocking warning + risk flag).
# unknown  — phone or proxy country could not be determined.
GeoMatchStatus = Literal["match", "mismatch", "unknown"]


class GeoMatch(BaseModel):
    """Verdict on whether an account's proxy and phone number agree on geo."""

    status: GeoMatchStatus
    phone_country: str | None = None
    proxy_country: str | None = None
    lang_country: str | None = None
    lang_matches: bool | None = None
    message: str | None = None
