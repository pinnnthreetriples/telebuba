"""Tests for the Gemini HTTP gateway (``core.gemini``) using respx."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from core.gemini import generate_text
from schemas.gemini import GeminiRequest

_ENDPOINT = r".*generateContent.*"


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
async def test_generate_text_non_200_is_error() -> None:
    with respx.mock:
        respx.post(url__regex=_ENDPOINT).mock(return_value=httpx.Response(429, text="rate limited"))

        result = await generate_text(_request())

    assert result.status == "error"
    assert "429" in (result.error or "")


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
