"""Tests for the Telemetr.io HTTP gateway (``core.telemetr``) using respx."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
import respx

from core.config import settings
from core.telemetr import _get_client, close_telemetr_client, search_catalog
from schemas.telemetr import (
    TELEMETR_MAX_LIMIT,
    TELEMETR_MAX_TITLE_LENGTH,
    TelemetrSearchRequest,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_ENDPOINT = r".*/catalog/search.*"
pytestmark = pytest.mark.usefixtures("isolated_telemetr_client")


def _request(**overrides: object) -> TelemetrSearchRequest:
    payload: dict[str, object] = {"api_key": "tm-key", "term": "crypto", "limit": 30}
    payload.update(overrides)
    return TelemetrSearchRequest.model_validate(payload)


def _ok_body(*rows: Mapping[str, object]) -> dict[str, object]:
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
async def test_rate_limited_then_success_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    """429 is retryable: one extra call costs 2 of 1000 free monthly requests, no ban risk."""
    monkeypatch.setattr(settings.telemetr, "max_retries", 1)
    monkeypatch.setattr(settings.telemetr, "retry_backoff_seconds", 0.0)
    with respx.mock:
        route = respx.get(url__regex=_ENDPOINT).mock(
            side_effect=[
                httpx.Response(429, text="quota"),
                httpx.Response(200, json=_ok_body({"peer": "after-quota"})),
            ],
        )

        result = await search_catalog(_request())

    assert result.status == "ok"
    assert route.call_count == 2
    assert [item.username for item in result.items] == ["after-quota"]


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
async def test_non_ascii_api_key_is_an_error_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Headers are encoded while the request is built, so this never reaches HTTPError."""
    monkeypatch.setattr(settings.telemetr, "max_retries", 0)
    with respx.mock:
        route = respx.get(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(200, json=_ok_body()),
        )

        result = await search_catalog(_request(api_key="ключ"))

    assert result.status == "error"
    assert route.call_count == 0


@pytest.mark.asyncio
async def test_malformed_base_url_is_an_error_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """``httpx.InvalidURL`` is not an ``HTTPError`` subclass, so it needs its own catch."""
    monkeypatch.setattr(settings.telemetr, "max_retries", 0)
    monkeypatch.setattr(settings.telemetr, "base_url", "http://telemetr.io:port")

    result = await search_catalog(_request())

    assert result.status == "error"
    assert result.error is not None
    assert "InvalidURL" in result.error


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
                        # Nothing but the sigil: survives a pre-strip guard, then fails
                        # the schema's min_length and raises out of a never-raises module.
                        {"peer": "@"},
                        {"peer": " @ "},
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
async def test_unusable_counts_become_unknown_without_losing_their_row() -> None:
    """One absurd count must not cost the run: the write happens after every source merges."""
    with respx.mock:
        respx.get(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json=_ok_body(
                    {"peer": "@valid", "members_count": 12345},
                    # Beyond what SQLite can store: OverflowError on the write.
                    {"peer": "@huge", "members_count": 2**70},
                    {"peer": "@negative", "members_count": -5},
                    # ``isinstance(True, int)`` holds, so a JSON bool would read as 1.
                    {"peer": "@boolean", "members_count": True},
                ),
            ),
        )

        result = await search_catalog(_request())

    assert result.status == "ok"
    assert [item.username for item in result.items] == ["valid", "huge", "negative", "boolean"]
    assert [item.members_count for item in result.items] == [12345, None, None, None]


@pytest.mark.asyncio
async def test_over_long_title_is_truncated_not_dropped() -> None:
    """An unbounded title would be re-serialised into every board poll of the run."""
    with respx.mock:
        respx.get(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json=_ok_body({"peer": "@verbose", "title": "t" * 5000}),
            ),
        )

        result = await search_catalog(_request())

    assert result.status == "ok"
    assert result.items[0].username == "verbose"
    assert result.items[0].title == "t" * TELEMETR_MAX_TITLE_LENGTH


@pytest.mark.asyncio
async def test_oversized_response_is_capped_at_the_limit_ceiling() -> None:
    """``limit`` on the wire is advisory; a long body must not be parsed in full."""
    with respx.mock:
        respx.get(url__regex=_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json=_ok_body(*({"peer": f"chan{index}"} for index in range(500))),
            ),
        )

        result = await search_catalog(_request())

    assert result.status == "ok"
    assert len(result.items) == TELEMETR_MAX_LIMIT


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
        first = _get_client()

    await close_telemetr_client()
    await close_telemetr_client()
    # A later call transparently rebuilds the client — a fresh object, not the
    # closed one (asserting "not None" would pass even if the holder never cleared).
    assert _get_client() is not first
