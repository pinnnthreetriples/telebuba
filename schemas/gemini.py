"""Pydantic schemas for the Gemini text-generation gateway.

Flow between ``services/warming.py`` (which asks for a chat line) and
``core/gemini.py`` (the only module that talks HTTP to Google). No behaviour.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

GeminiStatus = Literal["ok", "error"]


class GeminiRequest(BaseModel):
    api_key: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: float = Field(ge=0.0, le=2.0)
    max_output_tokens: int = Field(ge=1, le=2048)


class GeminiResult(BaseModel):
    status: GeminiStatus
    text: str | None = None
    error: str | None = None
