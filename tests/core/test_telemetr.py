"""Catalogue contract of the Telemetr.io gateway (``core.telemetr``) using respx.

A ``CatalogItem`` carries no handle, so a candidate only exists once its
``internal_id`` has been resolved through /channels/info-batch. Filter resolution and
status classification live in ``test_telemetr_status.py``.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from core.telemetr import _get_client, close_telemetr_client, search_catalog
from schemas.telemetr import TELEMETR_MAX_TITLE_LENGTH
from tests.core.telemetr_fixtures import (
    SEARCH,
    catalog_item,
    chat_info,
    mock_batch,
    mock_search,
    request,
    search_body,
)

pytestmark = pytest.mark.usefixtures("isolated_telemetr_client")


@pytest.mark.asyncio
async def test_catalogue_row_becomes_a_candidate_with_a_resolved_handle() -> None:
    """The regression test: a CatalogItem has no handle, so one must come from the batch.

    Against the old parser this row has no ``peer`` and is dropped, leaving a run that
    reports "ok" with nothing in it.
    """
    with respx.mock:
        mock_search(catalog_item())
        mock_batch(chat_info())

        result = await search_catalog(request())

    assert result.status == "ok"
    assert [item.username for item in result.items] == ["cryptonews"]
    assert result.items[0].title == "Crypto News"
    assert result.items[0].members_count == 12345
    # Provenance: what Telemetr itself filed the channel under, so a filter is verifiable.
    assert result.items[0].country == "turkey"
    assert result.items[0].language == "tr"


@pytest.mark.asyncio
async def test_batch_resolve_asks_for_every_collected_id() -> None:
    with respx.mock:
        mock_search(
            catalog_item(internal_id="ch-1"),
            catalog_item(internal_id="ch-2", title="Altcoins"),
        )
        batch = mock_batch(
            chat_info(internal_id="ch-1"),
            chat_info(internal_id="ch-2", link="https://t.me/altcoins"),
        )

        result = await search_catalog(request())

    sent = batch.calls.last.request
    assert sent.headers["x-api-key"] == "tm-key"
    assert httpx.URL(str(sent.url)).params["ids"] == "ch-1,ch-2"
    assert [item.username for item in result.items] == ["cryptonews", "altcoins"]


@pytest.mark.parametrize(
    "info",
    [
        chat_info(internal_id="ch-2", link=None),
        chat_info(internal_id="ch-2", link="https://t.me/+AbCdEf123"),
        chat_info(internal_id="ch-2", peer="Group", link="https://t.me/some_group"),
    ],
    ids=["no-link", "invite-only", "group"],
)
@pytest.mark.asyncio
async def test_rows_without_a_public_handle_are_dropped(info: dict[str, object]) -> None:
    """No link, a private invite and a group all leave nothing to comment under."""
    with respx.mock:
        mock_search(catalog_item(internal_id="ch-1"), catalog_item(internal_id="ch-2"))
        mock_batch(chat_info(internal_id="ch-1"), info)

        result = await search_catalog(request())

    assert result.status == "ok"
    assert [item.username for item in result.items] == ["cryptonews"]


@pytest.mark.asyncio
async def test_id_missing_from_the_batch_is_dropped() -> None:
    """The spec documents no behaviour for an unknown id, so absence is the assumption."""
    with respx.mock:
        mock_search(catalog_item(internal_id="ch-1"), catalog_item(internal_id="gone"))
        mock_batch(chat_info(internal_id="ch-1"))

        result = await search_catalog(request())

    assert result.status == "ok"
    assert [item.username for item in result.items] == ["cryptonews"]


@pytest.mark.parametrize(
    "link",
    ["https://t.me/cryptonews", "http://t.me/cryptonews", "t.me/cryptonews", "t.me/cryptonews/42"],
    ids=["https", "http", "bare-host", "post-link"],
)
@pytest.mark.asyncio
async def test_handle_is_derived_from_the_link(link: str) -> None:
    with respx.mock:
        mock_search(catalog_item())
        mock_batch(chat_info(link=link))

        result = await search_catalog(request())

    assert [item.username for item in result.items] == ["cryptonews"]


@pytest.mark.asyncio
async def test_search_sends_key_header_and_member_bounds() -> None:
    with respx.mock:
        search = mock_search()

        await search_catalog(request(members_min=500, members_max=90000))

    sent = search.calls.last.request
    assert sent.headers["x-api-key"] == "tm-key"
    params = httpx.URL(str(sent.url)).params
    assert params["term"] == "crypto"
    assert params["search_in_about"] == "true"
    assert params["limit"] == "30"
    assert params["members_min"] == "500"
    assert params["members_max"] == "90000"


@pytest.mark.asyncio
async def test_junk_rows_are_dropped_without_failing_the_source() -> None:
    """A row without an internal_id has no key into the batch, so it cannot be resolved."""
    with respx.mock:
        respx.get(url__regex=SEARCH).mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        {"title": "no internal_id"},
                        catalog_item(internal_id="   "),
                        catalog_item(internal_id=42),
                        "not a dict",
                        catalog_item(internal_id="ch-1", members_count="many"),
                    ],
                },
            ),
        )
        mock_batch(chat_info(internal_id="ch-1"))

        result = await search_catalog(request())

    assert result.status == "ok"
    assert [item.username for item in result.items] == ["cryptonews"]
    # A non-int count is treated as unknown rather than coerced.
    assert result.items[0].members_count is None


@pytest.mark.asyncio
async def test_unusable_counts_become_unknown_without_losing_their_row() -> None:
    """One absurd count must not cost the run: the write happens after every source merges."""
    names = ("ok", "huge", "negative", "boolean")
    with respx.mock:
        mock_search(
            catalog_item(internal_id="ok", members_count=12345),
            # Beyond what SQLite can store: OverflowError on the write.
            catalog_item(internal_id="huge", members_count=2**70),
            catalog_item(internal_id="negative", members_count=-5),
            # ``isinstance(True, int)`` holds, so a JSON bool would read as 1.
            catalog_item(internal_id="boolean", members_count=True),
        )
        mock_batch(
            *(chat_info(internal_id=name, link=f"https://t.me/{name}") for name in names),
        )

        result = await search_catalog(request())

    assert [item.username for item in result.items] == list(names)
    assert [item.members_count for item in result.items] == [12345, None, None, None]


@pytest.mark.asyncio
async def test_over_long_text_is_truncated_not_dropped() -> None:
    """An unbounded title would be re-serialised into every board poll of the run."""
    with respx.mock:
        mock_search(catalog_item(title="t" * 5000, country="c" * 5000))
        mock_batch(chat_info())

        result = await search_catalog(request())

    assert result.items[0].title == "t" * TELEMETR_MAX_TITLE_LENGTH
    assert result.items[0].country == "c" * TELEMETR_MAX_TITLE_LENGTH


@pytest.mark.asyncio
async def test_oversized_response_is_capped_at_the_requested_limit() -> None:
    """``limit`` on the wire is advisory, and a row we do not keep still costs batch ids."""
    with respx.mock:
        mock_search(*(catalog_item(internal_id=f"ch-{index}") for index in range(50)))
        batch = mock_batch(
            *(
                chat_info(internal_id=f"ch-{index}", link=f"https://t.me/chan{index}")
                for index in range(50)
            ),
        )

        result = await search_catalog(request(limit=5))

    assert len(result.items) == 5
    assert httpx.URL(str(batch.calls.last.request.url)).params["ids"] == "ch-0,ch-1,ch-2,ch-3,ch-4"


@pytest.mark.asyncio
async def test_total_count_makes_truncation_visible() -> None:
    with respx.mock:
        mock_search(catalog_item(), count=4211)
        mock_batch(chat_info())

        result = await search_catalog(request())

    assert result.total_count == 4211
    assert len(result.items) == 1


@pytest.mark.asyncio
async def test_bare_list_payload_is_accepted() -> None:
    """Tolerate an unwrapped array so an envelope change does not kill the source."""
    with respx.mock:
        respx.get(url__regex=SEARCH).mock(return_value=httpx.Response(200, json=[catalog_item()]))
        mock_batch(chat_info())

        result = await search_catalog(request())

    assert result.status == "ok"
    assert [item.username for item in result.items] == ["cryptonews"]
    # A bare array carries no total.
    assert result.total_count is None


@pytest.mark.asyncio
async def test_unexpected_payload_shape_yields_no_items() -> None:
    with respx.mock:
        respx.get(url__regex=SEARCH).mock(return_value=httpx.Response(200, json={"odd": "shape"}))
        batch = mock_batch()

        result = await search_catalog(request())

    assert result.status == "ok"
    assert result.items == []
    # No ids to resolve means no second request, so an empty page costs one unit.
    assert batch.call_count == 0


@pytest.mark.asyncio
async def test_empty_items_envelope_still_reports_its_total() -> None:
    with respx.mock:
        respx.get(url__regex=SEARCH).mock(
            return_value=httpx.Response(200, json=search_body(count=17)),
        )

        result = await search_catalog(request())

    assert result.status == "ok"
    assert result.total_count == 17


@pytest.mark.asyncio
async def test_shared_client_is_reused_across_calls() -> None:
    with respx.mock:
        mock_search()

        await search_catalog(request())
        first = _get_client()
        await search_catalog(request())

    assert _get_client() is first


@pytest.mark.asyncio
async def test_close_client_is_idempotent() -> None:
    with respx.mock:
        mock_search()
        await search_catalog(request())
        first = _get_client()

    await close_telemetr_client()
    await close_telemetr_client()
    # A later call transparently rebuilds the client — a fresh object, not the
    # closed one (asserting "not None" would pass even if the holder never cleared).
    assert _get_client() is not first
