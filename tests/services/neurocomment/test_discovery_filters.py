"""Pure discovery filters: language detection, access, and the two admission gates."""

from __future__ import annotations

import pytest

from schemas.neurocomment_discovery_request import DiscoverySearchRequest
from services.neurocomment._discovery_filters import (
    access_of,
    admit_at_qualification,
    admit_at_search,
    detect_language,
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
        ("durov", None, "open"),
    ],
)
def test_access_of(username: str | None, join_request: bool | None, expected: str) -> None:  # noqa: FBT001
    assert access_of(username, join_request) == expected


def _search(request: DiscoverySearchRequest, **hit: object) -> str | None:
    fields: dict[str, object] = {"kind": "channel", "access": "open", "ref": "x", "seen": set()}
    fields.update(hit)
    return admit_at_search(request=request, **fields)  # type: ignore[arg-type]


def test_admit_at_search_kind() -> None:
    assert _search(_request(kind="channels"), kind="group") == "kind"
    assert _search(_request(kind="groups"), kind="channel") == "kind"
    assert _search(_request(kind="groups"), kind="group") is None
    assert _search(_request(kind="all"), kind="group") is None
    assert _search(_request(kind="all"), kind="channel") is None


def test_admit_at_search_access_decides_only_the_subscription_leg() -> None:
    assert _search(_request(access="subscription"), access="open") == "access"
    assert _search(_request(access="subscription"), access="subscription") is None
    assert _search(_request(access="open"), access="subscription") == "access"
    assert _search(_request(access="join_request"), access="subscription") == "access"
    # open vs join_request is the probe's to tell, so neither rejects here.
    assert _search(_request(access="open"), access="join_request") is None
    assert _search(_request(access="join_request"), access="open") is None
    assert _search(_request(access="any"), access="subscription") is None


def test_admit_at_search_seen() -> None:
    assert _search(_request(), ref="durov", seen={"durov"}) == "seen"
    assert _search(_request(), ref="durov", seen={"other"}) is None
    assert _search(_request(hide_seen=False), ref="durov", seen={"durov"}) is None


def test_admit_at_search_reports_the_first_gate_that_fails() -> None:
    assert _search(_request(kind="channels"), kind="group", ref="d", seen={"d"}) == "kind"


def _qualify(request: DiscoverySearchRequest, **learnt: object) -> str | None:
    fields: dict[str, object] = {
        "title": "Crypto news",
        "about": None,
        "comments_enabled": None,
        "access": None,
        "language": None,
    }
    fields.update(learnt)
    return admit_at_qualification(request=request, **fields)  # type: ignore[arg-type]


def test_admit_at_qualification_comments() -> None:
    assert _qualify(_request(comments="on"), comments_enabled=False) == "comments"
    assert _qualify(_request(comments="off"), comments_enabled=True) == "comments"
    assert _qualify(_request(comments="on"), comments_enabled=True) is None
    assert _qualify(_request(comments="off"), comments_enabled=False) is None
    assert _qualify(_request(comments="any"), comments_enabled=False) is None
    # Unknown never rejects.
    assert _qualify(_request(comments="on"), comments_enabled=None) is None


def test_admit_at_qualification_access() -> None:
    assert _qualify(_request(access="open"), access="join_request") == "access"
    assert _qualify(_request(access="join_request"), access="open") == "access"
    assert _qualify(_request(access="open"), access="open") is None
    assert _qualify(_request(access="open"), access=None) is None
    assert _qualify(_request(access="any"), access="join_request") is None


def test_admit_at_qualification_language() -> None:
    assert _qualify(_request(language="ru"), language="en") == "language"
    assert _qualify(_request(language="ru"), language="ru") is None
    assert _qualify(_request(language="ru"), language=None) is None
    assert _qualify(_request(language="any"), language="other") is None


def test_admit_at_qualification_category() -> None:
    assert _qualify(_request(category="food"), title="Crypto news") == "category"
    assert _qualify(_request(category="crypto"), title="Crypto news") is None
    assert _qualify(_request(category="food"), title="Дайджест", about="рецепты дня") is None
    assert _qualify(_request(category="any"), title="Anything") is None


def test_admit_at_qualification_order_is_comments_access_language_category() -> None:
    request = _request(comments="on", access="open", language="ru", category="food")
    assert _qualify(request, comments_enabled=False, access="join_request") == "comments"
    assert _qualify(request, comments_enabled=True, access="join_request") == "access"
    assert _qualify(request, comments_enabled=True, access="open", language="en") == "language"
    assert _qualify(request, comments_enabled=True, access="open", language="ru") == "category"
