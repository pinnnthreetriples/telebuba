"""Tests for the OpenAI HTTP gateway (``core.openai``) using respx."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from core.openai import generate_text
from schemas.gemini import GeminiRequest

_ENDPOINT = r".*chat/completions.*"
pytestmark = pytest.mark.usefixtures("isolated_openai_client")


def _request(
    *,
    prompt: str = "solve this",
    image_b64: str | None = None,
    image_mime: str = "image/jpeg",
    response_schema_json: dict[str, object] | None = None,
) -> GeminiRequest:
    return GeminiRequest(
        api_key="sk-test",
        prompt=prompt,
        model="gpt-4o",
        temperature=0.0,
        max_output_tokens=300,
        image_b64=image_b64,
        image_mime=image_mime,
        response_schema_json=response_schema_json,
    )


def _ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.mark.asyncio
async def test_generate_text_returns_message_content() -> None:
    with respx.mock:
        respx.post(url__regex=_ENDPOINT).mock(return_value=_ok("hi there"))
        result = await generate_text(_request())
    assert result.status == "ok"
    assert result.text == "hi there"


@pytest.mark.asyncio
async def test_sends_bearer_auth_header() -> None:
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(return_value=_ok("ok"))
        await generate_text(_request())
    assert route.calls.last.request.headers["authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_image_added_as_data_uri_image_part() -> None:
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(return_value=_ok("{}"))
        await generate_text(_request(image_b64="aW1n", image_mime="image/png"))
    content = json.loads(route.calls.last.request.content)["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "solve this"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/png;base64,aW1n"


@pytest.mark.asyncio
async def test_response_schema_becomes_json_schema_format() -> None:
    schema: dict[str, object] = {"type": "object", "properties": {}}
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(return_value=_ok("{}"))
        await generate_text(_request(response_schema_json=schema))
    body = json.loads(route.calls.last.request.content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == schema


@pytest.mark.asyncio
async def test_no_image_part_when_unset() -> None:
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(return_value=_ok("ok"))
        await generate_text(_request())
    content = json.loads(route.calls.last.request.content)["messages"][0]["content"]
    assert content == [{"type": "text", "text": "solve this"}]


@pytest.mark.asyncio
async def test_persistent_429_is_rate_limited() -> None:
    with respx.mock:
        respx.post(url__regex=_ENDPOINT).mock(return_value=httpx.Response(429, text="slow down"))
        result = await generate_text(_request())
    assert result.status == "rate_limited"


@pytest.mark.asyncio
async def test_http_error_status_is_error() -> None:
    with respx.mock:
        respx.post(url__regex=_ENDPOINT).mock(return_value=httpx.Response(401, text="bad key"))
        result = await generate_text(_request())
    assert result.status == "error"


@pytest.mark.asyncio
async def test_missing_choices_is_error() -> None:
    with respx.mock:
        respx.post(url__regex=_ENDPOINT).mock(return_value=httpx.Response(200, json={}))
        result = await generate_text(_request())
    assert result.status == "error"


@pytest.mark.asyncio
async def test_transport_error_is_error() -> None:
    with respx.mock:
        respx.post(url__regex=_ENDPOINT).mock(side_effect=httpx.ConnectError("boom"))
        result = await generate_text(_request())
    assert result.status == "error"
