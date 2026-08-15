"""Tests for the OpenAI HTTP gateway (``core.openai``) using respx."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from core.config import settings
from core.openai import generate_text, generate_text_deepseek
from schemas.gemini import GeminiRequest

_ENDPOINT = r".*chat/completions.*"
pytestmark = pytest.mark.usefixtures("isolated_openai_client")


def _request(
    *,
    prompt: str = "solve this",
    image_b64: str | None = None,
    image_mime: str = "image/jpeg",
    response_schema_json: dict[str, object] | None = None,
    response_json_object: bool = False,
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
        response_json_object=response_json_object,
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
async def test_the_deepseek_entry_point_talks_to_deepseek() -> None:
    """The whole point of the second entry point: same code, different host.

    Asserted on the URL rather than on ``settings`` because that is the thing a
    misconfiguration would get wrong silently — a DeepSeek key posted to OpenAI's
    endpoint is a 401 the operator would read as a bad key.
    """
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(return_value=_ok("ok"))
        result = await generate_text_deepseek(_request())
    assert result.status == "ok"
    assert str(route.calls.last.request.url) == f"{settings.deepseek.base_url}/chat/completions"


@pytest.mark.asyncio
async def test_the_default_entry_point_still_talks_to_openai() -> None:
    """The other half: adding a provider must not move the one that was already here."""
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(return_value=_ok("ok"))
        await generate_text(_request())
    assert str(route.calls.last.request.url) == f"{settings.openai.base_url}/chat/completions"


@pytest.mark.asyncio
async def test_deepseek_is_told_not_to_think_by_default() -> None:
    """The truncation trap, pinned: V4 reasons by default and bills it to max_tokens.

    Omit the field and a 256-token comment budget goes to the thoughts, leaving a
    stump the gateway reports as ``Truncated: hit max_tokens`` — i.e. every comment
    fails. ``core.gemini`` sends ``thinkingBudget`` explicitly for the same reason.
    """
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(return_value=_ok("ok"))
        await generate_text_deepseek(_request())
    body = json.loads(route.calls.last.request.content)
    assert body["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_a_caller_that_asks_for_reasoning_gets_it_on_deepseek() -> None:
    """``thinking_budget`` stays meaningful rather than being silently ignored.

    DeepSeek grades effort instead of counting tokens, so a budget only switches
    reasoning on and the provider's own default effort stands.
    """
    request = _request()
    request.thinking_budget = 512
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(return_value=_ok("ok"))
        await generate_text_deepseek(request)
    body = json.loads(route.calls.last.request.content)
    assert body["thinking"] == {"type": "enabled"}


@pytest.mark.asyncio
async def test_openai_is_never_sent_the_thinking_field() -> None:
    """OpenAI rejects the parameter outright, so the capability flag has to gate it."""
    request = _request()
    request.thinking_budget = 512
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(return_value=_ok("ok"))
        await generate_text(request)
    assert "thinking" not in json.loads(route.calls.last.request.content)


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
async def test_json_object_is_the_mode_deepseek_actually_accepts() -> None:
    """DeepSeek documents ``response_format.type`` as only ``text`` or ``json_object``."""
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(return_value=_ok("{}"))
        await generate_text(_request(response_json_object=True))
    body = json.loads(route.calls.last.request.content)
    assert body["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_json_object_wins_over_a_schema_carried_for_another_provider() -> None:
    """A caller may hold a schema for Gemini and still need DeepSeek's schema-less mode."""
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(return_value=_ok("{}"))
        await generate_text(
            _request(response_schema_json={"type": "object"}, response_json_object=True),
        )
    body = json.loads(route.calls.last.request.content)
    assert body["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_the_new_field_leaves_every_existing_payload_untouched() -> None:
    """Default ``False`` must mean the request is byte-identical to before."""
    with respx.mock:
        route = respx.post(url__regex=_ENDPOINT).mock(return_value=_ok("ok"))
        await generate_text(_request())
    body = json.loads(route.calls.last.request.content)
    assert "response_format" not in body


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


@pytest.mark.asyncio
async def test_length_finish_reason_is_its_own_failure_not_partial_text() -> None:
    """A max_tokens cut is a failure, never a short success.

    Mirrors the Gemini gateway: the solver runs here whenever the operator picks the
    openai provider, and truncated JSON must not read as an undecidable captcha. It
    carries its OWN status because re-asking unchanged truncates identically — a
    caller that can shrink its ask needs to tell it from a transport error, and one
    that cannot still sees a non-``ok`` status and gives up exactly as before.
    """
    with respx.mock:
        respx.post(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": '{"action":"click_button","reason'},
                            "finish_reason": "length",
                        },
                    ],
                },
            ),
        )
        result = await generate_text(_request())

    assert result.status == "truncated"
    assert result.text is None
    assert "max_tokens" in (result.error or "")


@pytest.mark.asyncio
async def test_stop_finish_reason_still_returns_text() -> None:
    """Only ``length`` is rejected — a ``stop`` finish is the ordinary success path."""
    with respx.mock:
        respx.post(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "decided"}, "finish_reason": "stop"},
                    ],
                },
            ),
        )
        result = await generate_text(_request())

    assert result.status == "ok"
    assert result.text == "decided"
