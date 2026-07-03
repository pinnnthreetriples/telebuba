"""Tests for the Gemini HTTP gateway (``core.gemini``) using respx."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio
import respx

from core.config import settings
from core.gemini import _get_client, close_gemini_client, generate_text
from schemas.gemini import GeminiRequest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_ENDPOINT = r".*generateContent.*"


@pytest_asyncio.fixture(autouse=True)
async def _close_shared_client() -> AsyncIterator[None]:
    """The gateway reuses a module-level client; close it between tests."""
    yield
    await close_gemini_client()


def _request() -> GeminiRequest:
    return GeminiRequest(
        api_key="test-key",
        prompt="say hi",
        model="gemini-2.5-flash",
        temperature=0.5,
        max_output_tokens=50,
    )


@pytest.mark.asyncio
async def test_generate_text_returns_joined_parts() -> None:
    with respx.mock:
        parts = [{"text": "Hey "}, {"text": "there"}]
        respx.post(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": parts}}]},
            ),
        )

        result = await generate_text(_request())

    assert result.status == "ok"
    assert result.text == "Hey there"


@pytest.mark.asyncio
async def test_generate_text_sends_api_key_header() -> None:
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
            ),
        )

        await generate_text(_request())

    assert route.calls.last.request.headers["x-goog-api-key"] == "test-key"


@pytest.mark.asyncio
async def test_response_schema_json_added_to_generation_config() -> None:
    schema: dict[str, object] = {"type": "object", "properties": {"action": {"type": "string"}}}
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]},
            ),
        )
        request = GeminiRequest(
            api_key="k",
            prompt="solve",
            model="gemini-2.5-flash",
            temperature=0.0,
            max_output_tokens=200,
            response_schema_json=schema,
        )
        await generate_text(request)

    body = json.loads(route.calls.last.request.content)
    generation = body["generationConfig"]
    assert generation["responseSchema"] == schema
    assert generation["responseMimeType"] == "application/json"


@pytest.mark.asyncio
async def test_no_response_schema_when_unset() -> None:
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
            ),
        )
        await generate_text(_request())

    generation = json.loads(route.calls.last.request.content)["generationConfig"]
    assert "responseSchema" not in generation
    assert "responseMimeType" not in generation


@pytest.mark.asyncio
async def test_image_added_as_inline_data_part() -> None:
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]},
            ),
        )
        request = GeminiRequest(
            api_key="k",
            prompt="read the captcha",
            model="gemini-2.5-flash",
            temperature=0.0,
            max_output_tokens=200,
            image_b64="aW1n",
            image_mime="image/png",
        )
        await generate_text(request)

    parts = json.loads(route.calls.last.request.content)["contents"][0]["parts"]
    assert parts[0] == {"text": "read the captcha"}
    assert parts[1] == {"inlineData": {"mimeType": "image/png", "data": "aW1n"}}


@pytest.mark.asyncio
async def test_no_image_part_when_unset() -> None:
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
            ),
        )
        await generate_text(_request())

    parts = json.loads(route.calls.last.request.content)["contents"][0]["parts"]
    assert parts == [{"text": "say hi"}]


@pytest.mark.asyncio
async def test_generate_text_persistent_429_is_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit #5: a persistent 429 surfaces distinctly, not flattened to a generic error."""
    monkeypatch.setattr(settings.gemini, "max_retries", 1)
    monkeypatch.setattr(settings.gemini, "retry_backoff_seconds", 0.0)
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(429, text="rate limited"),
        )

        result = await generate_text(_request())

    assert result.status == "rate_limited"
    assert "429" in (result.error or "")
    assert route.call_count == 2  # original + one retry


@pytest.mark.asyncio
async def test_generate_text_retries_429_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit #5: a 429 followed by a 200 succeeds after one retry."""
    monkeypatch.setattr(settings.gemini, "max_retries", 1)
    monkeypatch.setattr(settings.gemini, "retry_backoff_seconds", 0.0)
    responses = [
        httpx.Response(429, text="slow down"),
        httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}),
    ]
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(side_effect=responses)

        result = await generate_text(_request())

    assert result.status == "ok"
    assert result.text == "hi"
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_generate_text_shared_client_reused_and_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit #5: the AsyncClient is reused across calls and closed by close_gemini_client."""
    monkeypatch.setattr(settings.gemini, "max_retries", 0)
    with respx.mock:
        respx.post(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
            ),
        )
        await generate_text(_request())
        first_client = _get_client()
        await generate_text(_request())
        assert _get_client() is first_client  # not rebuilt per call

    await close_gemini_client()
    assert first_client.is_closed
    assert _get_client() is not first_client  # lazily rebuilt after close


@pytest.mark.asyncio
async def test_generate_text_transport_error_is_error() -> None:
    with respx.mock:
        respx.post(url__regex=_ENDPOINT).mock(side_effect=httpx.ConnectError("boom"))

        result = await generate_text(_request())

    assert result.status == "error"
    assert "ConnectError" in (result.error or "")


@pytest.mark.asyncio
async def test_generate_text_missing_candidates_is_error() -> None:
    with respx.mock:
        respx.post(url__regex=_ENDPOINT).mock(return_value=httpx.Response(200, json={}))

        result = await generate_text(_request())

    assert result.status == "error"
    assert result.text is None


@pytest.mark.parametrize(
    "body",
    [
        {"candidates": "not-a-list"},
        {"candidates": [{}]},
        {"candidates": [{"content": {"parts": "not-a-list"}}]},
        {"candidates": [{"content": {"parts": [{"no_text": "x"}]}}]},
    ],
)
@pytest.mark.asyncio
async def test_generate_text_malformed_payload_is_error(body: dict[str, object]) -> None:
    with respx.mock:
        respx.post(url__regex=_ENDPOINT).mock(return_value=httpx.Response(200, json=body))

        result = await generate_text(_request())

    assert result.status == "error"
    assert result.text is None
