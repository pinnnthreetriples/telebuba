"""Pydantic schemas for the Gemini text-generation gateway.

Flow between ``services/warming.py`` (which asks for a chat line) and
``core/gemini.py`` (the only module that talks HTTP to Google). No behaviour.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ``truncated`` is an error that says WHICH one: the answer hit ``max_tokens``
# mid-word. Its own member because retrying it unchanged is guaranteed to
# truncate again — a caller that can shrink its ask (``services.neuroshilling``)
# needs to tell it apart, and one that cannot treats it as any other error.
GeminiStatus = Literal["ok", "error", "rate_limited", "truncated"]


class GeminiRequest(BaseModel):
    api_key: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: float = Field(ge=0.0, le=2.0)
    # Ceiling for thoughts + answer combined: on a thinking model (the 2.5 family)
    # Gemini bills reasoning tokens against ``maxOutputTokens``, so a request that
    # enables ``thinking_budget`` needs room for both halves, not just the reply.
    max_output_tokens: int = Field(ge=1, le=2048)
    # Reasoning-token allowance. ``0`` disables thinking, which is what short-text
    # callers want: it makes ``max_output_tokens`` mean what they assume (a budget
    # for the reply). Left at 0, a 2.5 model silently spent the whole budget on
    # thoughts and returned a mid-word stump. Only the captcha solver opts in.
    thinking_budget: int = Field(default=0, ge=0, le=2048)
    # Optional JSON-Schema for server-side structured output (Gemini
    # ``responseSchema``); an opaque schema dict, not inter-layer domain data.
    response_schema_json: dict[str, object] | None = None
    # Ask an OpenAI-format provider for ``response_format: {"type": "json_object"}``
    # instead of the schema-enforced mode. DeepSeek documents ``response_format.type``
    # as "one of ``text`` or ``json_object``" and answers a ``json_schema`` request
    # with an error, so a caller that wants JSON from it has no other way to say so.
    # ``core.gemini`` ignores this field, exactly as it ignores nothing else here:
    # Gemini's own structured output is ``response_schema_json`` and stays that.
    # False by default, so no existing payload changes by a byte.
    response_json_object: bool = False
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
