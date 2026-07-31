"""Shared respx scaffolding for the ``core.telemetr`` tests.

Every builder mirrors a published OpenAPI shape: a ``CatalogItem`` carries no handle
at all (no ``peer``, no ``username``, no ``link``), ``ChatInfo.peer`` is a chat-type
discriminator, and ``ChatInfo.link`` is the only handle-bearing field in the whole
API. The previous fixtures invented a ``peer`` handle, which is why the catalogue
returning zero candidates on every run stayed invisible.

Lives outside ``conftest.py`` because these are plain builders, not fixtures, and both
``test_telemetr.py`` (catalogue contract) and ``test_telemetr_status.py`` (filters and
status classes) need them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import respx

from core.telemetr import _get_client
from schemas.telemetr import TelemetrSearchRequest

if TYPE_CHECKING:
    import pytest

SEARCH = r".*/catalog/search.*"
BATCH = r".*/channels/info-batch.*"
COUNTRIES = r".*/dictionaries/countries.*"

# Documented dictionary items: an ``id`` is a slug, not an ISO-3166 code.
COUNTRY_DICTIONARY = [
    {"id": "ukraine", "name": "Ukraine", "channels_count": 32779, "participants_count": 295013777},
    {"id": "turkey", "name": "Turkey", "channels_count": 4211, "participants_count": 51000000},
    {
        "id": "united-kingdom",
        "name": "United Kingdom",
        "channels_count": 1902,
        "participants_count": 9100000,
    },
]


def request(**overrides: object) -> TelemetrSearchRequest:
    """A configured search request; override any field per test."""
    payload: dict[str, object] = {"api_key": "tm-key", "term": "crypto", "limit": 30}
    payload.update(overrides)
    return TelemetrSearchRequest.model_validate(payload)


def catalog_item(**overrides: object) -> dict[str, object]:
    """A ``CatalogItem`` exactly as documented — every property, no handle among them."""
    item: dict[str, object] = {
        "internal_id": "ch-1",
        "title": "Crypto News",
        "description": "daily crypto digest",
        "members_count": 12345,
        "privacy": "Public",
        "photo_url": None,
        "country": "turkey",
        "language": "tr",
        "category": "Cryptocurrencies",
        "verified": True,
        "members_change": None,
        "post_views": 4200,
        "post_views_24h": 900,
        "er": 12.5,
        "ads_index": None,
        "mentions": None,
    }
    item.update(overrides)
    return item


def chat_info(**overrides: object) -> dict[str, object]:
    """A ``ChatInfo`` as documented: ``peer`` is the chat type, ``link`` the handle."""
    info: dict[str, object] = {
        "internal_id": "ch-1",
        "peer": "Channel",
        "title": "Crypto News",
        "members_count": 12345,
        "verified": True,
        "photo_url": None,
        "country": "turkey",
        "language": "tr",
        "category": "Cryptocurrencies",
        "telegram_id": 1234567890,
        "link": "https://t.me/cryptonews",
        "description": "daily crypto digest",
        "creation_date": "2021-04-02T10:00:00Z",
    }
    info.update(overrides)
    return info


def search_body(*items: object, count: int | None = None) -> dict[str, object]:
    """The ``{items, count, audience_count}`` catalogue envelope."""
    return {
        "items": list(items),
        "count": len(items) if count is None else count,
        "audience_count": 999999,
    }


def mock_search(*items: object, count: int | None = None) -> respx.Route:
    """Route /catalog/search to a 200 carrying ``items``."""
    return respx.get(url__regex=SEARCH).mock(
        return_value=httpx.Response(200, json=search_body(*items, count=count)),
    )


def mock_batch(*channels: object) -> respx.Route:
    """Route /channels/info-batch to a 200 carrying ``channels``."""
    return respx.get(url__regex=BATCH).mock(
        return_value=httpx.Response(200, json={"channels": list(channels)}),
    )


def mock_countries() -> respx.Route:
    """Route the country dictionary to its documented bare array.

    Only countries have one: a language ``id`` already is the ISO-639-1 code the form
    sends, so the adapter never fetches that dictionary.
    """
    return respx.get(url__regex=COUNTRIES).mock(
        return_value=httpx.Response(200, json=COUNTRY_DICTIONARY),
    )


class AttemptCounter:
    """Counts calls into the shared client.

    A key that will not encode and an unparseable base URL both fail while httpx builds
    the request, so respx never sees them and its ``call_count`` cannot tell one attempt
    from three — this wrapper can.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.count = 0
        client = _get_client()
        inner = client.get

        async def _counted(
            url: str,
            *,
            headers: dict[str, str],
            params: dict[str, str | int] | None = None,
        ) -> httpx.Response:
            self.count += 1
            return await inner(url, headers=headers, params=params)

        monkeypatch.setattr(client, "get", _counted)
