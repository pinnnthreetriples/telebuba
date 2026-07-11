"""Pydantic schemas for the Gemini text-generation gateway.

Flow between ``services/warming.py`` (which asks for a chat line) and
``core/gemini.py`` (the only module that talks HTTP to Google). No behaviour.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

GeminiStatus = Literal["ok", "error", "rate_limited"]


class GeminiRequest(BaseModel):
    api_key: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: float = Field(ge=0.0, le=2.0)
    max_output_tokens: int = Field(ge=1, le=2048)
    # Optional JSON-Schema for server-side structured output (Gemini
    # ``responseSchema``); an opaque schema dict, not inter-layer domain data.
    response_schema_json: dict[str, object] | None = None
    # Optional inline image (base64) for a multimodal request — e.g. an image
    # captcha the vision model must read. ``image_mime`` is ignored when
    # ``image_b64`` is None; the model must be vision-capable (gemini-2.5-flash is).
    image_b64: str | None = None
    image_mime: str = Field(default="image/jpeg", min_length=1)
    # Per-request overrides for the gateway's rate-limit handling. ``None`` falls
    # back to ``settings.gemini.*`` — only callers that want to self-throttle (the
    # neurocomment generator) set them, so captcha/warming calls are unaffected.
    max_retries: int | None = Field(default=None, ge=0, le=5)
    min_interval_seconds: float | None = Field(default=None, ge=0.0)


class GeminiResult(BaseModel):
    status: GeminiStatus
    text: str | None = None
    error: str | None = None
