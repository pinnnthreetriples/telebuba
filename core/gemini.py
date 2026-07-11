"""Gemini text-generation gateway.

The only module that talks HTTP to Google's Generative Language API. Services
never call ``httpx`` directly — they pass a :class:`GeminiRequest` here and get
a typed :class:`GeminiResult` back (never an exception).

Endpoint (June 2026): ``POST {base_url}/models/{model}:generateContent`` with the
key in the ``x-goog-api-key`` header.
"""

from __future__ import annotations

import asyncio
import time
from typing import cast

import httpx

from core.config import settings
from schemas.gemini import GeminiRequest, GeminiResult

_HTTP_OK = 200
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR_MIN = 500


class _ThrottleState:
    """Shared last-call clock for the inter-request spacing gate.

    A single lock serialises the wait so concurrent generations queue and fire
    ``min_interval`` apart, keeping a burst under a per-minute API quota.
    """

    lock = asyncio.Lock()
    last_call: float = 0.0  # time.monotonic() of the previous slot


_throttle = _ThrottleState()


async def _await_slot(min_interval: float) -> None:
    """Sleep until ``min_interval`` has elapsed since the previous Gemini call.

    ``min_interval <= 0`` disables the gate entirely (and never touches the
    shared clock, so callers that opt out don't perturb opted-in spacing).
    """
    if min_interval <= 0:
        return
    async with _throttle.lock:
        wait = _throttle.last_call + min_interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _throttle.last_call = time.monotonic()


class _ClientHolder:
    client: httpx.AsyncClient | None = None


_holder = _ClientHolder()


def _get_client() -> httpx.AsyncClient:
    """Return the shared AsyncClient, creating it on first use.

    Reused across calls so the warming/neurocomment hot path does not pay a
    fresh connection pool + TLS handshake every request.
    """
    if _holder.client is None:
        _holder.client = httpx.AsyncClient(timeout=settings.gemini.timeout_seconds)
    return _holder.client


async def close_gemini_client() -> None:
    """Close the shared AsyncClient. Called from the app lifespan on shutdown."""
    if _holder.client is not None:
        await _holder.client.aclose()
        _holder.client = None


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
    parts: list[dict[str, object]] = [{"text": request.prompt}]
    if request.image_b64 is not None:
        # Multimodal: append the image as inline base64 data (Gemini `inlineData`).
        parts.append({"inlineData": {"mimeType": request.image_mime, "data": request.image_b64}})
    return {
        "contents": [{"role": "user", "parts": parts}],
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


def _is_transient(status_code: int) -> bool:
    # 429 (rate limit) and 5xx are worth one more attempt; a 4xx (bad key,
    # bad request) will not fix itself, so it fails fast.
    return status_code == _HTTP_TOO_MANY_REQUESTS or status_code >= _HTTP_SERVER_ERROR_MIN


def _classify_response(response: httpx.Response) -> GeminiResult:
    if response.status_code == _HTTP_TOO_MANY_REQUESTS:
        # Surface rate-limiting distinctly so callers can back off rather than
        # treat it as a permanent failure.
        return GeminiResult(
            status="rate_limited",
            error=f"HTTP 429: {response.text[:200]}",
        )
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


async def generate_text(request: GeminiRequest) -> GeminiResult:
    """Call Gemini and return generated text, classifying any failure typed-ly.

    Never raises: HTTP errors, timeouts, and unexpected payload shapes map to
    ``GeminiResult(status="error", ...)`` and a 429 maps to ``status="rate_limited"``
    so callers can differentiate. Retries a transient failure (429 / 5xx /
    transport error) up to ``request.max_retries`` (or ``settings.gemini.max_retries``)
    times with a short backoff, and spaces calls by ``request.min_interval_seconds``
    (or the config default). The shared AsyncClient is reused across calls.
    """
    interval = (
        request.min_interval_seconds
        if request.min_interval_seconds is not None
        else settings.gemini.min_interval_seconds
    )
    await _await_slot(interval)
    client = _get_client()
    max_retries = (
        request.max_retries if request.max_retries is not None else settings.gemini.max_retries
    )
    attempts = max_retries + 1
    result = GeminiResult(status="error", error="No attempt made")
    for attempt in range(attempts):
        try:
            response = await client.post(
                _endpoint(request.model),
                headers={"x-goog-api-key": request.api_key},
                json=_payload(request),
            )
        except httpx.HTTPError as exc:
            result = GeminiResult(status="error", error=f"{type(exc).__name__}: {exc}")
            transient = True
        else:
            result = _classify_response(response)
            transient = _is_transient(response.status_code)
        if not transient or attempt == attempts - 1:
            return result
        await asyncio.sleep(settings.gemini.retry_backoff_seconds)
    return result
