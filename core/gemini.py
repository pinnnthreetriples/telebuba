"""Gemini text-generation gateway.

The only module that talks HTTP to Google's Generative Language API. Services
never call ``httpx`` directly — they pass a :class:`GeminiRequest` here and get
a typed :class:`GeminiResult` back (never an exception).

Endpoint (June 2026): ``POST {base_url}/models/{model}:generateContent`` with the
key in the ``x-goog-api-key`` header.
"""

from __future__ import annotations

from typing import cast

import httpx

from core.config import settings
from schemas.gemini import GeminiRequest, GeminiResult

_HTTP_OK = 200


def _endpoint(model: str) -> str:
    return f"{settings.gemini.base_url}/models/{model}:generateContent"


def _payload(request: GeminiRequest) -> dict[str, object]:
    generation_config: dict[str, object] = {
        "temperature": request.temperature,
        "maxOutputTokens": request.max_output_tokens,
    }
    if request.response_schema_json is not None:
        # Server-side structured output: Gemini validates against the schema and
        # returns JSON, so parse-fails are effectively impossible on our side.
        generation_config["responseSchema"] = request.response_schema_json
        generation_config["responseMimeType"] = "application/json"
    return {
        "contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
        "generationConfig": generation_config,
    }


def _extract_text(body: dict[str, object]) -> str | None:
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    first = candidates[0]
    if not isinstance(first, dict):
        return None
    content = cast("dict[str, object]", first).get("content")
    if not isinstance(content, dict):
        return None
    parts = cast("dict[str, object]", content).get("parts")
    if not isinstance(parts, list):
        return None
    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        value = cast("dict[str, object]", part).get("text")
        if isinstance(value, str):
            texts.append(value)
    joined = "".join(texts).strip()
    return joined or None


async def generate_text(request: GeminiRequest) -> GeminiResult:
    """Call Gemini and return generated text, classifying any failure as an error.

    Never raises: HTTP errors, timeouts, and unexpected payload shapes all map
    to ``GeminiResult(status="error", ...)`` so the warming loop can carry on.
    """
    try:
        async with httpx.AsyncClient(timeout=settings.gemini.timeout_seconds) as client:
            response = await client.post(
                _endpoint(request.model),
                headers={"x-goog-api-key": request.api_key},
                json=_payload(request),
            )
    except httpx.HTTPError as exc:
        return GeminiResult(status="error", error=f"{type(exc).__name__}: {exc}")

    if response.status_code != _HTTP_OK:
        return GeminiResult(
            status="error",
            error=f"HTTP {response.status_code}: {response.text[:200]}",
        )

    try:
        body = response.json()
    except ValueError as exc:
        return GeminiResult(status="error", error=f"Invalid JSON: {exc}")

    text = _extract_text(body) if isinstance(body, dict) else None
    if text is None:
        return GeminiResult(status="error", error="No text in Gemini response")
    return GeminiResult(status="ok", text=text)
