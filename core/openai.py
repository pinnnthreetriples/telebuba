"""OpenAI-format text/vision gateway — the captcha-solver alternative, and DeepSeek.

The only module that talks HTTP in this wire format. Mirrors ``core.gemini``:
services pass the shared :class:`GeminiRequest` (the provider-neutral LLM
contract) and get a typed :class:`GeminiResult` back — never an exception.

Two providers ride it, and the ONLY thing separating them is which settings block
supplies the endpoint and the retry budget (``config``, defaulting to OpenAI's):
the captcha solver when the operator selects the ``openai`` provider, and DeepSeek
for every text generation (:func:`generate_text_deepseek`). One gateway rather
than two because DeepSeek publishes this exact format — a second module would be
this one with a different base URL.

Endpoint: ``POST {base_url}/chat/completions`` with a ``Bearer`` key. Images ride
as a base64 ``image_url`` data-URI content part; structured output uses
``response_format: json_schema``. DeepSeek's models are text-only, so the image
part must never reach it — the routing that guarantees that lives in the callers.

The one place the two providers' payloads differ is ``thinking``: DeepSeek accepts
it and reasons by default, OpenAI rejects the field outright. ``sends_thinking`` on
the settings class carries that, because it is a property of the API rather than
anything an operator may set.
"""

from __future__ import annotations

import asyncio
from typing import cast

import httpx

from core.config import OpenAISettings, settings
from schemas.gemini import GeminiRequest, GeminiResult

_HTTP_OK = 200
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR_MIN = 500


class _ClientHolder:
    client: httpx.AsyncClient | None = None


_holder = _ClientHolder()


def _get_client() -> httpx.AsyncClient:
    """Return the shared AsyncClient, creating it on first use (reused across calls).

    No timeout bound here since the second provider arrived: one client serves both,
    and each request carries its own provider's timeout instead (see
    :func:`generate_text`). A client-level default would silently apply OpenAI's
    number to whichever provider happened to create it first.
    """
    if _holder.client is None:
        _holder.client = httpx.AsyncClient()
    return _holder.client


async def close_openai_client() -> None:
    """Close the shared AsyncClient. Called from the app lifespan on shutdown."""
    if _holder.client is not None:
        await _holder.client.aclose()
        _holder.client = None


def _endpoint(config: OpenAISettings) -> str:
    return f"{config.base_url}/chat/completions"


def _payload(request: GeminiRequest, provider: OpenAISettings) -> dict[str, object]:
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
    if provider.sends_thinking:
        # Always explicit, exactly as ``core.gemini`` sends ``thinkingBudget`` — and for
        # the identical reason. DeepSeek-V4 defaults ``thinking`` to enabled at "high"
        # effort and bills the thoughts to ``max_tokens``, so omitting this hands a
        # 256-token comment budget to the reasoning and leaves nothing for the reply.
        # ``thinking_budget`` is the request's provider-neutral way to ask for
        # reasoning: 0 (every short-text caller) means off. DeepSeek grades effort
        # rather than counting tokens, so a non-zero budget only turns it on and lets
        # the provider's own default effort stand — nothing here invents a mapping
        # from a token count to "low"/"high"/"max".
        payload["thinking"] = {"type": "enabled" if request.thinking_budget else "disabled"}
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
    choices = body.get("choices") if isinstance(body, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else None
    if isinstance(first, dict) and first.get("finish_reason") == "length":
        # Same contract as the Gemini gateway: a max_tokens cut yields a mid-word
        # fragment — invalid JSON under response_format — so it is an error, not a
        # short success. The solver runs on this path whenever the operator picks
        # the openai provider, and a truncated decision reads as an undecidable
        # captcha rather than a budget that needs raising.
        return GeminiResult(status="error", error="Truncated: hit max_tokens")
    text = _extract_text(body) if isinstance(body, dict) else None
    if text is None:
        return GeminiResult(status="error", error="No text in OpenAI response")
    return GeminiResult(status="ok", text=text)


async def generate_text(
    request: GeminiRequest,
    *,
    config: OpenAISettings | None = None,
) -> GeminiResult:
    """Call chat/completions and return the text, classifying failures typed-ly.

    Never raises: HTTP errors, timeouts, and unexpected payloads map to
    ``GeminiResult(status="error", ...)``; a 429 maps to ``status="rate_limited"``.
    Retries a transient failure (429 / 5xx / transport error) up to
    ``config.max_retries`` times with a short backoff.

    ``config`` picks the provider — the endpoint, timeout and retry budget — while
    the key, model and temperature stay on the request, because those are the
    caller's to vary per campaign. ``None`` means OpenAI, so every existing caller
    keeps its behaviour without naming a provider it never had to think about.
    """
    provider = config or settings.openai
    client = _get_client()
    attempts = provider.max_retries + 1
    result = GeminiResult(status="error", error="No attempt made")
    for attempt in range(attempts):
        try:
            response = await client.post(
                _endpoint(provider),
                headers={"Authorization": f"Bearer {request.api_key}"},
                json=_payload(request, provider),
                timeout=provider.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            result = GeminiResult(status="error", error=f"{type(exc).__name__}: {exc}")
            transient = True
        else:
            result = _classify_response(response)
            transient = _is_transient(response.status_code)
        if not transient or attempt == attempts - 1:
            return result
        await asyncio.sleep(provider.retry_backoff_seconds)
    return result


async def generate_text_deepseek(request: GeminiRequest) -> GeminiResult:
    """:func:`generate_text` against DeepSeek instead of OpenAI.

    A function rather than a bound ``partial`` so ``settings.deepseek`` is read when
    the call happens: the settings object is swapped wholesale by tests and by the
    env reload, and a partial would freeze whichever one existed at import time.
    """
    return await generate_text(request, config=settings.deepseek)
