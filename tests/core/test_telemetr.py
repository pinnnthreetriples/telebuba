"""Tests for the Telemetr.io HTTP gateway (``core.telemetr``) using respx."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.config import settings
from core.telemetr import _get_client, close_telemetr_client, search_catalog
from schemas.telemetr import TelemetrSearchRequest

_ENDPOINT = r".*/catalog/search.*"
pytestmark = pytest.mark.usefixtures("isolated_telemetr_client")


def _request(**overrides: object) -> TelemetrSearchRequest:
    payload: dict[str, object] = {"api_key": "tm-key", "term": "crypto", "limit": 30}
    payload.update(overrides)
    return TelemetrSearchRequest.model_validate(payload)


def _ok_body(*rows: dict[str, object]) -> dict[str, object]:
    return {"items": list(rows)}


@pytest.mark.asyncio
async def test_search_parses_catalogue_rows() -> None:
    with respx.mock:
        respx.get(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json=_ok_body(
                    {"peer": "@cryptonews", "title": "Crypto News", "members_count": 12345},
                    {"peer": "altcoins", "title": "Altcoins"},
                ),
            ),
        )

        result = await search_catalog(_request())

    assert result.status == "ok"
    assert [item.username for item in result.items] == ["cryptonews", "altcoins"]
    assert result.items[0].title == "Crypto News"
    assert result.items[0].members_count == 12345
    # A row without a count stays unknown rather than defaulting to 0.
    assert result.items[1].members_count is None


@pytest.mark.asyncio
async def test_search_sends_key_header_and_filters() -> None:
    with respx.mock:
        route = respx.get(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(200, json=_ok_body()),
        )

        await search_catalog(
            _request(country="ae", language="ar", members_min=500, members_max=90000),
        )

    request = route.calls.last.request
    assert request.headers["x-api-key"] == "tm-key"
    params = httpx.URL(str(request.url)).params
    assert params["term"] == "crypto"
    assert params["search_in_about"] == "true"
    assert params["limit"] == "30"
    assert params["country"] == "ae"
    assert params["language"] == "ar"
    assert params["members_min"] == "500"
    assert params["members_max"] == "90000"


@pytest.mark.asyncio
async def test_unset_filters_are_omitted_not_sent_as_none() -> None:
    with respx.mock:
        route = respx.get(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(200, json=_ok_body()),
        )

        await search_catalog(_request())

    params = httpx.URL(str(route.calls.last.request.url)).params
    assert "country" not in params
    assert "language" not in params
    assert "members_min" not in params
    assert "members_max" not in params


@pytest.mark.asyncio
async def test_missing_key_short_circuits_without_a_request() -> None:
    """An unconfigured source is skipped, never an error — and costs no socket."""
    with respx.mock:
        route = respx.get(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(200, json=_ok_body()),
        )

        result = await search_catalog(_request(api_key=""))

    assert result.status == "not_configured"
    assert result.items == []
    assert route.call_count == 0


@pytest.mark.asyncio
async def test_rate_limited_maps_to_its_own_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.telemetr, "max_retries", 0)
    with respx.mock:
        respx.get(url__regex=_ENDPOINT).mock(return_value=httpx.Response(429, text="quota"))

        result = await search_catalog(_request())

    assert result.status == "rate_limited"
    assert result.error is not None
    assert "429" in result.error


@pytest.mark.asyncio
async def test_client_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.telemetr, "max_retries", 2)
    with respx.mock:
        route = respx.get(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(401, text="bad key"),
        )

        result = await search_catalog(_request())

    assert result.status == "error"
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_server_error_is_retried_then_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.telemetr, "max_retries", 2)
    monkeypatch.setattr(settings.telemetr, "retry_backoff_seconds", 0.0)
    with respx.mock:
        route = respx.get(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(503, text="down"),
        )

        result = await search_catalog(_request())

    assert result.status == "error"
    assert route.call_count == 3


@pytest.mark.asyncio
async def test_server_error_then_success_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.telemetr, "max_retries", 1)
    monkeypatch.setattr(settings.telemetr, "retry_backoff_seconds", 0.0)
    with respx.mock:
        respx.get(url__regex=_ENDPOINT).mock(
            side_effect=[
                httpx.Response(500, text="boom"),
                httpx.Response(200, json=_ok_body({"peer": "late"})),
            ],
        )

        result = await search_catalog(_request())

    assert result.status == "ok"
    assert [item.username for item in result.items] == ["late"]


@pytest.mark.asyncio
async def test_transport_error_is_retried_then_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.telemetr, "max_retries", 1)
    monkeypatch.setattr(settings.telemetr, "retry_backoff_seconds", 0.0)
    with respx.mock:
        route = respx.get(url__regex=_ENDPOINT).mock(
            side_effect=httpx.ConnectTimeout("timed out"),
        )

        result = await search_catalog(_request())

    assert result.status == "error"
    assert result.error is not None
    assert "ConnectTimeout" in result.error
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_malformed_json_is_an_error_not_a_crash() -> None:
    with respx.mock:
        respx.get(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(200, text="not json"),
        )

        result = await search_catalog(_request())

    assert result.status == "error"
    assert result.error is not None
    assert "Invalid JSON" in result.error


@pytest.mark.asyncio
async def test_junk_rows_are_dropped_without_failing_the_source() -> None:
    """A row with no handle cannot be linked to a campaign, so it is skipped."""
    with respx.mock:
        respx.get(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        {"title": "no peer"},
                        {"peer": "   "},
                        {"peer": 42},
                        "not a dict",
                        {"peer": "@good", "members_count": "many"},
                    ],
                },
            ),
        )

        result = await search_catalog(_request())

    assert result.status == "ok"
    assert [item.username for item in result.items] == ["good"]
    # A non-int count is treated as unknown rather than coerced.
    assert result.items[0].members_count is None


@pytest.mark.asyncio
async def test_bare_list_payload_is_accepted() -> None:
    """Tolerate an unwrapped array so an envelope change does not kill the source."""
    with respx.mock:
        respx.get(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(200, json=[{"peer": "bare"}]),
        )

        result = await search_catalog(_request())

    assert result.status == "ok"
    assert [item.username for item in result.items] == ["bare"]


@pytest.mark.asyncio
async def test_unexpected_payload_shape_yields_no_items() -> None:
    with respx.mock:
        respx.get(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(200, json={"unexpected": "shape"}),
        )

        result = await search_catalog(_request())

    assert result.status == "ok"
    assert result.items == []


@pytest.mark.asyncio
async def test_shared_client_is_reused_across_calls() -> None:
    with respx.mock:
        respx.get(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(200, json=_ok_body()),
        )

        await search_catalog(_request())
        first = _get_client()
        await search_catalog(_request())

    assert _get_client() is first


@pytest.mark.asyncio
async def test_close_client_is_idempotent() -> None:
    with respx.mock:
        respx.get(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(200, json=_ok_body()),
        )
        await search_catalog(_request())

    await close_telemetr_client()
    await close_telemetr_client()
    # A later call transparently rebuilds the client.
    assert _get_client() is not None
