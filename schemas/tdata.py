"""Pydantic schemas for tdata → .session conversion.

These are the only types the rest of the codebase needs to know about; the
opentele2 SDK lives behind ``core.tdata_import`` and never leaks out.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 - Pydantic needs the runtime class for field schema.
from typing import Literal

from pydantic import BaseModel, Field, model_validator

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
    """User-supplied tdata.zip ready for conversion.

    Either ``content`` (in-memory bytes) or ``content_path`` (filesystem) must
    be set. UI uploads stream to a temp file and pass ``content_path`` so a
    1 GB archive never sits in RAM; CLI / test callers may still pass ``content``.
    """

    model_config = {"arbitrary_types_allowed": True}

    filename: str = Field(min_length=1)
    content: bytes = Field(default=b"")
    content_path: Path | None = None
    label: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _exactly_one_payload(self) -> TdataConvertRequest:
        if not self.content and self.content_path is None:
            msg = "TdataConvertRequest needs either content or content_path"
            raise ValueError(msg)
        return self


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
