"""OpenAI (ChatGPT) text/vision gateway — the captcha-solver alternative LLM.

The only module that talks HTTP to OpenAI. Mirrors ``core.gemini``: services
pass the shared :class:`GeminiRequest` (the provider-neutral LLM contract) and
get a typed :class:`GeminiResult` back — never an exception. Used only by the
challenge solver when the operator selects the ``openai`` provider.

Endpoint: ``POST {base_url}/chat/completions`` with a ``Bearer`` key. Images ride
as a base64 ``image_url`` data-URI content part; structured output uses
``response_format: json_schema``.
"""

from __future__ import annotations

import asyncio
from typing import cast

import httpx

from core.config import settings
from schemas.gemini import GeminiRequest, GeminiResult

_HTTP_OK = 200
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR_MIN = 500


class _ClientHolder:
    client: httpx.AsyncClient | None = None


_holder = _ClientHolder()


def _get_client() -> httpx.AsyncClient:
    """Return the shared AsyncClient, creating it on first use (reused across calls)."""
    if _holder.client is None:
        _holder.client = httpx.AsyncClient(timeout=settings.openai.timeout_seconds)
    return _holder.client


async def close_openai_client() -> None:
    """Close the shared AsyncClient. Called from the app lifespan on shutdown."""
    if _holder.client is not None:
        await _holder.client.aclose()
        _holder.client = None


def _endpoint() -> str:
    return f"{settings.openai.base_url}/chat/completions"


def _payload(request: GeminiRequest) -> dict[str, object]:
    content: list[dict[str, object]] = [{"type": "text", "text": request.prompt}]
    if request.image_b64 is not None:
        # Vision: inline base64 as a data-URI image part.
        data_uri = f"data:{request.image_mime};base64,{request.image_b64}"
        content.append({"type": "image_url", "image_url": {"url": data_uri}})
    payload: dict[str, object] = {
        "model": request.model,
        "messages": [{"role": "user", "content": content}],
        "temperature": request.temperature,
        "max_tokens": request.max_output_tokens,
    }
    if request.response_schema_json is not None:
        # Server-side structured output: the model must return JSON matching the schema.
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "decision", "schema": request.response_schema_json},
        }
    return payload


def _extract_text(body: dict[str, object]) -> str | None:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = cast("dict[str, object]", first).get("message")
    if not isinstance(message, dict):
        return None
    text = cast("dict[str, object]", message).get("content")
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    return stripped or None


def _is_transient(status_code: int) -> bool:
    return status_code == _HTTP_TOO_MANY_REQUESTS or status_code >= _HTTP_SERVER_ERROR_MIN


def _classify_response(response: httpx.Response) -> GeminiResult:
    if response.status_code == _HTTP_TOO_MANY_REQUESTS:
        return GeminiResult(status="rate_limited", error=f"HTTP 429: {response.text[:200]}")
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
        return GeminiResult(status="error", error="No text in OpenAI response")
    return GeminiResult(status="ok", text=text)


async def generate_text(request: GeminiRequest) -> GeminiResult:
    """Call OpenAI chat/completions and return the text, classifying failures typed-ly.

    Never raises: HTTP errors, timeouts, and unexpected payloads map to
    ``GeminiResult(status="error", ...)``; a 429 maps to ``status="rate_limited"``.
    Retries a transient failure (429 / 5xx / transport error) up to
    ``settings.openai.max_retries`` times with a short backoff.
    """
    client = _get_client()
    attempts = settings.openai.max_retries + 1
    result = GeminiResult(status="error", error="No attempt made")
    for attempt in range(attempts):
        try:
            response = await client.post(
                _endpoint(),
                headers={"Authorization": f"Bearer {request.api_key}"},
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
        await asyncio.sleep(settings.openai.retry_backoff_seconds)
    return result
