"""Filter resolution, status classes and leak-free diagnostics of ``core.telemetr``.

The catalogue-parsing half of the contract lives in ``test_telemetr.py``; this file
covers what happens *around* a search: resolving an operator's ISO code to a
dictionary id, and telling "top up your plan" apart from "your key is wrong".
"""

from __future__ import annotations

import asyncio
from typing import get_args

import httpx
import pytest
import respx

from core.config import settings
from core.telemetr import _COUNTRY_NAME_BY_ALPHA2, search_catalog
from schemas.neurocomment_discovery import DiscoveryCountry
from tests.core.telemetr_fixtures import (
    BATCH,
    COUNTRIES,
    COUNTRY_DICTIONARY,
    SEARCH,
    AttemptCounter,
    catalog_item,
    chat_info,
    mock_batch,
    mock_countries,
    mock_search,
    request,
    search_body,
)

pytestmark = pytest.mark.usefixtures("isolated_telemetr_client")


def test_every_offered_country_has_an_alpha2_bridge() -> None:
    """The offered country list and the bridge that translates it must not drift apart.

    A forward guard, not a reproduction: the two agree today. ``schemas/`` may not import
    ``core``, so they are duplicated on purpose and only a test can hold them together —
    add a country to one side and the symptom is a filter that resolves to nothing
    against the live API only. ``tests/`` may import both sides.
    """
    assert set(get_args(DiscoveryCountry)) == set(_COUNTRY_NAME_BY_ALPHA2)


@pytest.mark.asyncio
async def test_an_unusable_country_dictionary_is_upstreams_fault_not_the_operators() -> None:
    """And it must not be cached: every later filter would be blamed for it.

    Caching an empty lookup answers a perfectly valid ``TR`` with ``unresolved_filter``
    for the life of the process.
    """
    with respx.mock:
        search = mock_search()
        respx.get(url__regex=COUNTRIES).mock(return_value=httpx.Response(200, json=[]))

        result = await search_catalog(request(country="TR"))

    assert result.status == "error"
    assert search.call_count == 0


@pytest.mark.asyncio
async def test_iso_country_code_is_resolved_to_a_dictionary_id() -> None:
    """An alpha-2 code matches neither an id ("turkey") nor a name ("Turkey")."""
    with respx.mock:
        search = mock_search()
        mock_countries()

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
        mock_countries()

        await search_catalog(request(country=value))

    assert httpx.URL(str(search.calls.last.request.url)).params["country"] == expected


@pytest.mark.asyncio
async def test_dictionaries_are_fetched_once_per_process() -> None:
    """Static reference data: caching it keeps the extra quota cost to one request."""
    with respx.mock:
        mock_search()
        countries = mock_countries()

        await search_catalog(request(country="TR"))
        await search_catalog(request(country="UA"))

    assert countries.call_count == 1


@pytest.mark.asyncio
async def test_concurrent_searches_share_one_dictionary_fetch() -> None:
    """A run fires every keyword's query at once, so they all miss the cache together.

    The sequential test above passes without any locking; only a real suspension point
    between the cache read and the fetch exposes it. Ten keywords with a country filter
    cost ten dictionary requests instead of one, against a 1000/month tier.
    """

    async def slow(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0)
        return httpx.Response(200, json=COUNTRY_DICTIONARY)

    with respx.mock:
        mock_search()
        countries = respx.get(url__regex=COUNTRIES).mock(side_effect=slow)

        results = await asyncio.gather(
            *(search_catalog(request(country="TR")) for _ in range(5)),
        )

    assert [result.status for result in results] == ["ok"] * 5
    assert countries.call_count == 1


@pytest.mark.asyncio
async def test_unresolvable_country_is_terminal_not_an_empty_ok() -> None:
    """A silent empty result set is exactly what hides this class of bug.

    Languages need no equivalent: their id IS the code the form sends, and the form's
    values are an allowlisted Literal, so a wrong one is a 422 long before this layer.
    """
    with respx.mock:
        search = mock_search()
        mock_countries()

        result = await search_catalog(request(country="ZZ"))

    assert result.status == "unresolved_filter"
    assert result.error is not None
    assert "ZZ" in result.error
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
        countries = mock_countries()

        await search_catalog(request())

    params = httpx.URL(str(search.calls.last.request.url)).params
    assert "country" not in params
    assert "language" not in params
    assert countries.call_count == 0


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
async def test_a_rate_limit_is_retried_but_keeps_its_own_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """429 is the one code in the table a backoff genuinely fixes.

    A run fires every keyword's query at once, so a free-tier rate limit is the expected
    reply rather than an exceptional one — and treating it as terminal made the whole run
    fail (and, with locale filters set, refuse to store anything) over a wait.
    """
    monkeypatch.setattr(settings.telemetr, "max_retries", 2)
    monkeypatch.setattr(settings.telemetr, "retry_backoff_seconds", 0.0)
    with respx.mock:
        search = respx.get(url__regex=SEARCH).mock(
            return_value=httpx.Response(429, text="slow down"),
        )

        result = await search_catalog(request())

    assert result.status == "rate_limited"
    assert search.call_count == 3


@pytest.mark.asyncio
async def test_a_rate_limit_that_clears_on_a_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.telemetr, "max_retries", 1)
    monkeypatch.setattr(settings.telemetr, "retry_backoff_seconds", 0.0)
    with respx.mock:
        mock_batch(chat_info())
        respx.get(url__regex=SEARCH).mock(
            side_effect=[
                httpx.Response(429, text="slow down"),
                httpx.Response(200, json=search_body(catalog_item())),
            ],
        )

        result = await search_catalog(request())

    assert result.status == "ok"
    assert [item.username for item in result.items] == ["cryptonews"]


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
    # Asserting the raw code point is absent proves nothing: UnicodeEncodeError renders it
    # ESCAPED ("\\xa0"), so that assertion passes with or without a scrub. What the scrub
    # actually guarantees is that no part of the key survives.
    assert "secret" not in result.error
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
async def test_a_batch_reply_without_a_channels_list_is_a_failure() -> None:
    """The original bug was a parser dropping every row while the source said "ok".

    One renamed field upstream reproduces it exactly, and the operator's only symptom
    would again be a board full of unfiltered channels.
    """
    with respx.mock:
        mock_search(catalog_item())
        respx.get(url__regex=BATCH).mock(return_value=httpx.Response(200, json={"items": []}))

        result = await search_catalog(request())

    assert result.status == "error"
    assert result.error is not None
    assert "No channels list" in result.error


@pytest.mark.asyncio
async def test_a_page_of_only_groups_is_an_ordinary_empty_result() -> None:
    """Not a failure: the filter did its job.

    Marking the source degraded over a keyword that simply matched nothing usable would
    block a filtered run for no reason.
    """
    with respx.mock:
        mock_search(catalog_item())
        mock_batch(chat_info(peer="Group"))

        result = await search_catalog(request())

    assert result.status == "ok"
    assert result.items == []


@pytest.mark.parametrize("status_code", [401, 426], ids=["auth_failed", "quota_exhausted"])
@pytest.mark.asyncio
async def test_an_upstream_body_never_carries_the_key_onward(status_code: int) -> None:
    """The body is now shown to the operator and persisted, so it has to be scrubbed.

    Pre-fix the caller discarded this text, which is the only reason it was safe; the
    credential statuses are exactly the ones a gateway quotes the presented key back in.
    """
    with respx.mock:
        respx.get(url__regex=SEARCH).mock(
            return_value=httpx.Response(
                status_code,
                text="No API key found for key 'tm-key': rejected",
            ),
        )

        result = await search_catalog(request())

    assert result.error is not None
    assert "tm-key" not in result.error


@pytest.mark.asyncio
async def test_a_key_split_by_truncation_still_does_not_leak() -> None:
    """Scrub before truncating: cutting first can slice the key and leave a fragment."""
    key = "tm-abcdefghijklmnop"
    with respx.mock:
        respx.get(url__regex=SEARCH).mock(
            return_value=httpx.Response(401, text=f"{'x' * 190}{key} rejected"),
        )

        result = await search_catalog(request(api_key=key))

    assert result.error is not None
    assert "tm-abcdefgh" not in result.error


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
