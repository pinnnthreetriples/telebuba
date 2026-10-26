"""Contracts for the seller's ``.json`` that ships beside a ``.session`` file.

The JSON carries the cloud password (2FA) and the Android device the session was
made on. ``password`` is a secret: it is excluded from ``repr`` and nothing may
log or echo it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Stable codes the API returns verbatim and the SPA translates (``shell.code.*``).
SessionJsonRefusalCode = Literal[
    "session_json_invalid",
    "session_json_phone_mismatch",
]


class SellerSessionMeta(BaseModel):
    """What a seller's session JSON said, after key synonyms are folded."""

    # ``repr=False``: Pydantic renders every field into ``__repr__`` and a repr in a
    # stack frame is what an error tracker ships.
    password: str | None = Field(default=None, repr=False)
    device_model: str | None = None
    # The OS string as the seller wrote it, e.g. ``Android 14``.
    system_version: str | None = None
    app_version: str | None = None
    lang_code: str | None = None
    system_lang_code: str | None = None
    # Digits only.
    phone: str | None = None
    user_id: int | None = None

    @property
    def is_android(self) -> bool:
        return (self.system_version or "").lower().startswith("android")
