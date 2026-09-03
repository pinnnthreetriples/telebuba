"""Pure discovery filters: language detection, access, and the two admission gates."""

from __future__ import annotations

import pytest

from schemas.neurocomment_discovery_request import DiscoverySearchRequest
from services.neurocomment._discovery_filters import (
    access_of,
    admit_at_qualification,
    admit_at_search,
    detect_language,
    is_private_ref,
    private_ref,
)


def _request(**overrides: object) -> DiscoverySearchRequest:
    payload: dict[str, object] = {"keywords": ["crypto"], "account_ids": ["a"]}
    payload.update(overrides)
    return DiscoverySearchRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", None),
        ("  123 #!? 🚀 ", None),
        ("Новости крипты каждый день", "ru"),
        ("Новини та події України", "uk"),
        ("Ґрунтовно про все", "uk"),
        ("Crypto news daily", "en"),
        ("Café résumé", "en"),
        ("加密货币新闻 BTC", "other"),
        ("日本語のチャンネル", "other"),
        # Majority wins: a Latin brand name inside a Russian title is still Russian.
        ("Bitcoin: новости, аналитика, прогнозы", "ru"),
    ],
)
def test_detect_language(text: str, expected: str | None) -> None:
    assert detect_language(text) == expected


@pytest.mark.parametrize(
    ("username", "join_request", "expected"),
    [
        (None, None, "subscription"),
        ("", True, "subscription"),
        ("  ", False, "subscription"),
        ("durov", True, "join_request"),
        ("durov", False, "open"),
        # A public handle with no word on the join gate is unknown, not open: "open" was
        # a badge nothing measured, and let the join_request filter delete the channel.
        ("durov", None, None),
    ],
)
def test_access_of(username: str | None, join_request: bool | None, expected: str | None) -> None:  # noqa: FBT001
    assert access_of(username, join_request) == expected


def test_private_refs() -> None:
    assert private_ref(123) == "id:123"
    assert is_private_ref("id:123") is True
    assert is_private_ref("durov") is False


def _search(request: DiscoverySearchRequest, **hit: object) -> str | None:
    fields: dict[str, object] = {"access": None, "ref": "x", "seen": set()}
    fields.update(hit)
    return admit_at_search(request=request, **fields)  # type: ignore[arg-type]


def test_admit_at_search_access_decides_only_the_subscription_leg() -> None:
    assert _search(_request(access="subscription"), access="open") == "access"
    # Unknown join gate, but a public handle: still not a subscription channel.
    assert _search(_request(access="subscription"), access=None) == "access"
    assert _search(_request(access="subscription"), access="subscription") is None
    assert _search(_request(access="open"), access="subscription") == "access"
    assert _search(_request(access="join_request"), access="subscription") == "access"
    # open vs join_request is the probe's to tell, so neither rejects here.
    assert _search(_request(access="open"), access="join_request") is None
    assert _search(_request(access="join_request"), access="open") is None
    assert _search(_request(access="open"), access=None) is None
    assert _search(_request(access="any"), access="subscription") is None


def test_admit_at_search_seen() -> None:
    assert _search(_request(), ref="durov", seen={"durov"}) == "seen"
    assert _search(_request(), ref="durov", seen={"other"}) is None
    assert _search(_request(hide_seen=False), ref="durov", seen={"durov"}) is None


def test_admit_at_search_reports_the_first_gate_that_fails() -> None:
    assert _search(_request(access="open"), access="subscription", ref="d", seen={"d"}) == "access"


def _qualify(request: DiscoverySearchRequest, **learnt: object) -> str | None:
    fields: dict[str, object] = {
        "comments_enabled": None,
        "access": None,
        "language": "en",
        "category_match": None,
    }
    fields.update(learnt)
    return admit_at_qualification(request=request, **fields)  # type: ignore[arg-type]


def test_admit_at_qualification_comments() -> None:
    assert _qualify(_request(comments="on"), comments_enabled=False) == "comments"
    assert _qualify(_request(comments="off"), comments_enabled=True) == "comments"
    assert _qualify(_request(comments="on"), comments_enabled=True) is None
    assert _qualify(_request(comments="off"), comments_enabled=False) is None
    assert _qualify(_request(comments="any"), comments_enabled=False) is None
    # Unknown never rejects — which is also how a group is handed in, since a megagroup's
    # ``comments_enabled`` is structurally False (comments ARE its messages).
    assert _qualify(_request(comments="on"), comments_enabled=None) is None


def test_admit_at_qualification_access() -> None:
    assert _qualify(_request(access="open"), access="join_request") == "access"
    assert _qualify(_request(access="join_request"), access="open") == "access"
    assert _qualify(_request(access="open"), access="open") is None
    assert _qualify(_request(access="open"), access=None) is None
    assert _qualify(_request(access="any"), access="join_request") is None


def test_admit_at_qualification_language() -> None:
    """The verdict's own reading, so the filter sees exactly what the board reports."""
    assert _qualify(_request(language="ru"), language="en") == "language"
    assert _qualify(_request(language="ru"), language="ru") is None
    # No letters at all: the language is unknown, and unknown never rejects.
    assert _qualify(_request(language="ru"), language=None) is None
    assert _qualify(_request(language="any"), language="other") is None


def test_admit_at_qualification_category() -> None:
    assert _qualify(_request(category="food"), category_match=False) == "category"
    assert _qualify(_request(category="crypto"), category_match=True) is None
    assert _qualify(_request(category="food"), category_match=None) is None
    assert _qualify(_request(category="any"), category_match=False) is None


def test_admit_at_qualification_order_is_comments_access_language_category() -> None:
    request = _request(comments="on", access="open", language="ru", category="food")
    assert _qualify(request, comments_enabled=False, access="join_request") == "comments"
    assert _qualify(request, comments_enabled=True, access="join_request") == "access"
    assert _qualify(request, comments_enabled=True, access="open") == "language"
    assert (
        _qualify(request, comments_enabled=True, access="open", language="ru", category_match=False)
        == "category"
    )
