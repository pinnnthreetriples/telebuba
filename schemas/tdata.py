"""Pydantic schemas for tdata → .session conversion.

These are the only types the rest of the codebase needs to know about; the
opentele2 SDK lives behind ``core.tdata_import`` and never leaks out.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TdataConvertStatus = Literal[
    "ok",
    "no_accounts",
    "invalid_zip",
    "too_many_files",
    "unsafe_path",
    "symlinks_not_allowed",
    "zip_too_large",
    "tdata_not_found",
    "conversion_error",
]


class TdataConvertRequest(BaseModel):
    """User-supplied tdata.zip ready for conversion."""

    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)
    label: str | None = Field(default=None, min_length=1)


class TdataAccountSummary(BaseModel):
    """One Telegram account extracted from a tdata payload.

    ``session_path`` points to a freshly written Telethon ``.session`` file in the
    configured sessions directory. The account is NOT verified yet — callers must
    run ``check_telegram_session`` before treating it as alive.
    """

    user_id: int | None = None
    session_path: str = Field(min_length=1)


class TdataConvertResult(BaseModel):
    """Outcome of one ``convert_tdata_zip`` call."""

    status: TdataConvertStatus
    accounts: list[TdataAccountSummary] = Field(default_factory=list)
    error: str | None = None
