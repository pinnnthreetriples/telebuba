"""Filter resolution, status classes and leak-free diagnostics of ``core.telemetr``.

The catalogue-parsing half of the contract lives in ``test_telemetr.py``; this file
covers what happens *around* a search: resolving an operator's ISO code to a
dictionary id, and telling "top up your plan" apart from "your key is wrong".
"""

from __future__ import annotations

import httpx
import pytest
import respx

from core.config import settings
from core.telemetr import search_catalog
from tests.core.telemetr_fixtures import (
    BATCH,
    COUNTRIES,
    SEARCH,
    AttemptCounter,
    catalog_item,
    chat_info,
    mock_batch,
    mock_dictionaries,
    mock_search,
    request,
    search_body,
)

pytestmark = pytest.mark.usefixtures("isolated_telemetr_client")


@pytest.mark.asyncio
async def test_iso_country_code_is_resolved_to_a_dictionary_id() -> None:
    """An alpha-2 code matches neither an id ("turkey") nor a name ("Turkey")."""
    with respx.mock:
        search = mock_search()
        mock_dictionaries()

        result = await search_catalog(request(country="TR", language="tr"))

    assert result.status == "ok"
    params = httpx.URL(str(search.calls.last.request.url)).params
    assert params["country"] == "turkey"
    # A language id already *is* an ISO-639-1 code, so it resolves to itself.
    assert params["language"] == "tr"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("GB", "united-kingdom"), ("united-kingdom", "united-kingdom"), ("Ukraine", "ukraine")],
    ids=["alpha2-multiword", "slug-verbatim", "english-name"],
)
@pytest.mark.asyncio
async def test_country_filter_accepts_code_slug_or_name(value: str, expected: str) -> None:
    with respx.mock:
        search = mock_search()
        mock_dictionaries()

        await search_catalog(request(country=value))

    assert httpx.URL(str(search.calls.last.request.url)).params["country"] == expected


@pytest.mark.asyncio
async def test_dictionaries_are_fetched_once_per_process() -> None:
    """Static reference data: caching it keeps the extra quota cost to one request."""
    with respx.mock:
        mock_search()
        countries, _languages = mock_dictionaries()

        await search_catalog(request(country="TR"))
        await search_catalog(request(country="UA"))

    assert countries.call_count == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("country", "ZZ"), ("language", "xx")],
    ids=["country", "language"],
)
@pytest.mark.asyncio
async def test_unresolvable_filter_is_terminal_not_an_empty_ok(field: str, value: str) -> None:
    """A silent empty result set is exactly what hides this class of bug."""
    with respx.mock:
        search = mock_search()
        mock_dictionaries()

        result = await search_catalog(request(**{field: value}))

    assert result.status == "unresolved_filter"
    assert result.error is not None
    assert value in result.error
    assert search.call_count == 0


@pytest.mark.asyncio
async def test_dictionary_failure_degrades_to_a_reported_error() -> None:
    """Better a reported error than sending a value the catalogue will silently ignore."""
    with respx.mock:
        search = mock_search()
        respx.get(url__regex=COUNTRIES).mock(return_value=httpx.Response(503, text="down"))

        result = await search_catalog(request(country="TR"))

    assert result.status == "error"
    assert search.call_count == 0


@pytest.mark.asyncio
async def test_unset_filters_cost_no_dictionary_request() -> None:
    with respx.mock:
        search = mock_search()
        countries, languages = mock_dictionaries()

        await search_catalog(request())

    params = httpx.URL(str(search.calls.last.request.url)).params
    assert "country" not in params
    assert "language" not in params
    assert countries.call_count == 0
    assert languages.call_count == 0


@pytest.mark.asyncio
async def test_missing_key_short_circuits_without_a_request() -> None:
    """An unconfigured source is skipped, never an error — and costs no socket."""
    with respx.mock:
        search = mock_search()

        result = await search_catalog(request(api_key=""))

    assert result.status == "not_configured"
    assert result.items == []
    assert search.call_count == 0


@pytest.mark.asyncio
async def test_whitespace_only_key_is_not_configured_not_an_auth_error() -> None:
    """Reporting "your key is wrong" for a key never set sends the operator hunting."""
    with respx.mock:
        search = mock_search()

        result = await search_catalog(request(api_key="   "))

    assert result.status == "not_configured"
    assert search.call_count == 0


@pytest.mark.parametrize(
    ("status_code", "status"),
    [
        (426, "quota_exhausted"),
        (412, "subscription_inactive"),
        (429, "rate_limited"),
        (400, "bad_request"),
        (401, "auth_failed"),
        (403, "forbidden"),
        (404, "not_found"),
    ],
)
@pytest.mark.asyncio
async def test_terminal_codes_map_to_their_own_status_and_are_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    status: str,
) -> None:
    """A retry against an exhausted quota spends a unit that is already gone."""
    monkeypatch.setattr(settings.telemetr, "max_retries", 2)
    monkeypatch.setattr(settings.telemetr, "retry_backoff_seconds", 0.0)
    with respx.mock:
        search = respx.get(url__regex=SEARCH).mock(
            return_value=httpx.Response(status_code, text="upstream says no"),
        )

        result = await search_catalog(request())

    assert result.status == status
    assert result.error is not None
    assert str(status_code) in result.error
    assert search.call_count == 1


@pytest.mark.asyncio
async def test_server_error_is_retried_then_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.telemetr, "max_retries", 2)
    monkeypatch.setattr(settings.telemetr, "retry_backoff_seconds", 0.0)
    with respx.mock:
        search = respx.get(url__regex=SEARCH).mock(return_value=httpx.Response(503, text="down"))

        result = await search_catalog(request())

    assert result.status == "error"
    assert search.call_count == 3


@pytest.mark.asyncio
async def test_server_error_then_success_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.telemetr, "max_retries", 1)
    monkeypatch.setattr(settings.telemetr, "retry_backoff_seconds", 0.0)
    with respx.mock:
        respx.get(url__regex=SEARCH).mock(
            side_effect=[
                httpx.Response(500, text="boom"),
                httpx.Response(200, json=search_body(catalog_item())),
            ],
        )
        mock_batch(chat_info())

        result = await search_catalog(request())

    assert result.status == "ok"
    assert [item.username for item in result.items] == ["cryptonews"]


@pytest.mark.asyncio
async def test_transport_error_is_retried_then_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.telemetr, "max_retries", 1)
    monkeypatch.setattr(settings.telemetr, "retry_backoff_seconds", 0.0)
    with respx.mock:
        search = respx.get(url__regex=SEARCH).mock(side_effect=httpx.ConnectTimeout("timed out"))

        result = await search_catalog(request())

    assert result.status == "error"
    assert result.error is not None
    assert "ConnectTimeout" in result.error
    assert search.call_count == 2


@pytest.mark.asyncio
async def test_batch_failure_is_reported_not_silently_empty() -> None:
    """The batch spends quota too, so its 426 must reach the operator as one."""
    with respx.mock:
        mock_search(catalog_item())
        respx.get(url__regex=BATCH).mock(return_value=httpx.Response(426, text="quota"))

        result = await search_catalog(request())

    assert result.status == "quota_exhausted"


@pytest.mark.asyncio
async def test_malformed_json_is_an_error_not_a_crash() -> None:
    with respx.mock:
        respx.get(url__regex=SEARCH).mock(return_value=httpx.Response(200, text="not json"))

        result = await search_catalog(request())

    assert result.status == "error"
    assert result.error is not None
    assert "Invalid JSON" in result.error


@pytest.mark.asyncio
async def test_non_ascii_api_key_errors_after_one_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Headers are encoded while the request is built, so this never reaches HTTPError.

    A key carrying a non-breaking space will not encode on a second attempt either, so
    retrying it would only spend a request and delay the honest error.
    """
    monkeypatch.setattr(settings.telemetr, "max_retries", 2)
    monkeypatch.setattr(settings.telemetr, "retry_backoff_seconds", 0.0)
    with respx.mock:
        search = mock_search()
        counter = AttemptCounter(monkeypatch)

        result = await search_catalog(request(api_key="tm-\xa0secret"))

    assert result.status == "error"
    assert result.error is not None
    assert "UnicodeEncodeError" in result.error
    # The exception text quotes the offending character; the reported error must not.
    assert "\xa0" not in result.error
    assert counter.count == 1
    assert search.call_count == 0


@pytest.mark.asyncio
async def test_illegal_header_value_error_never_quotes_the_key() -> None:
    """h11 renders an illegal header value verbatim, and that value is the API key."""
    with respx.mock:
        respx.get(url__regex=SEARCH).mock(
            side_effect=httpx.LocalProtocolError("Illegal header value b'tm-secret\\n'"),
        )

        result = await search_catalog(request(api_key="tm-secret"))

    assert result.status == "error"
    assert result.error is not None
    assert "tm-secret" not in result.error


@pytest.mark.asyncio
async def test_api_key_is_not_printable_by_accident() -> None:
    """A repr of the request rides along in tracebacks and log payloads."""
    assert "tm-key" not in repr(request())


@pytest.mark.asyncio
async def test_malformed_base_url_errors_after_one_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """``httpx.InvalidURL`` is not an ``HTTPError`` subclass, so it needs its own catch.

    And a base URL that does not parse never will, so this is reported, not retried.
    """
    monkeypatch.setattr(settings.telemetr, "max_retries", 2)
    monkeypatch.setattr(settings.telemetr, "retry_backoff_seconds", 0.0)
    monkeypatch.setattr(settings.telemetr, "base_url", "http://telemetr.io:port")
    counter = AttemptCounter(monkeypatch)

    result = await search_catalog(request())

    assert result.status == "error"
    assert result.error is not None
    assert "InvalidURL" in result.error
    assert counter.count == 1


@pytest.mark.asyncio
async def test_scheme_less_base_url_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """``UnsupportedProtocol`` *is* an ``HTTPError``, so only clause order saves it.

    It surfaces from inside the transport, and it is a configuration defect a second
    attempt cannot fix.
    """
    monkeypatch.setattr(settings.telemetr, "max_retries", 2)
    monkeypatch.setattr(settings.telemetr, "retry_backoff_seconds", 0.0)
    with respx.mock:
        search = respx.get(url__regex=SEARCH).mock(
            side_effect=httpx.UnsupportedProtocol("Request URL is missing an 'http://' protocol."),
        )

        result = await search_catalog(request())

    assert result.status == "error"
    assert search.call_count == 1
