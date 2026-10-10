"""The parser's filters, table by table, and the aggregate they judge."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from schemas.telegram_actions_user_parser import ChatHistoryMessage
from services.user_parser._collect import Collector, UserAggregate
from services.user_parser._filters import (
    FilterContext,
    apply_filters,
    message_counts,
    rejection,
)
from tests.services.user_parser.fakes import NOW, person
from tests.services.user_parser.helpers import parser_request


def _agg(
    *,
    count: int = 1,
    sources: tuple[str, ...] = ("@a",),
    **fields: Any,
) -> UserAggregate:
    return UserAggregate(
        user=person(1, **fields), message_count=count, sources=dict.fromkeys(sources)
    )


def _ctx(**overrides: Any) -> FilterContext:
    ids = {
        key: overrides.pop(key)
        for key in ("own_ids", "admin_ids", "collected_ids")
        if key in overrides
    }
    return FilterContext(request=parser_request(**overrides), **ids)


_ALL_OFF = {
    "skip_bots": False,
    "skip_deleted": False,
    "skip_scam": False,
    "exclude_own": False,
    "exclude_admins": False,
}


@pytest.mark.parametrize(
    ("user", "settings", "reason"),
    [
        ({"is_bot": True}, {}, "bot"),
        ({"is_deleted": True}, {}, "deleted"),
        ({"is_scam": True}, {}, "scam"),
        ({"is_fake": True}, {}, "scam"),
        ({"is_bot": True}, {"toggles": _ALL_OFF}, None),
        ({}, {"own_ids": frozenset({1})}, "own"),
        (
            {},
            {"own_ids": frozenset({1}), "toggles": {"exclude_own": False, "exclude_admins": False}},
            None,
        ),
        ({}, {"admin_ids": frozenset({1}), "toggles": {"exclude_admins": True}}, "admin"),
        ({}, {"admin_ids": frozenset({1})}, None),
        (
            {},
            {
                "collected_ids": frozenset({1}),
                "toggles": {"exclude_collected": True, "exclude_admins": False},
            },
            "collected",
        ),
        ({}, {"collected_ids": frozenset({1})}, None),
        ({"username": "Spam_Shop"}, {"blacklist": ["@spam_shop"]}, "blacklist"),
        ({"username": "spam"}, {"blacklist": ["https://t.me/SPAM"]}, "blacklist"),
        ({}, {"blacklist": ["1"]}, "blacklist"),
        ({"username": None}, {"blacklist": ["@", " "]}, None),
        ({"first_name": "Crypto SHOP"}, {"stop_words": ["shop"]}, "stop_word"),
        ({"username": "best_promo"}, {"stop_words": [" Promo "]}, "stop_word"),
        ({"first_name": "Анна", "last_name": "Магазин"}, {"stop_words": ["магазин"]}, "stop_word"),
        ({"first_name": "Анна"}, {"stop_words": ["   "]}, None),
        (
            {"username": None},
            {"toggles": {"with_username": True, "exclude_admins": False}},
            "no_username",
        ),
        (
            {"has_photo": False},
            {"toggles": {"with_photo": True, "exclude_admins": False}},
            "no_photo",
        ),
        (
            {"is_premium": False},
            {"toggles": {"premium_only": True, "exclude_admins": False}},
            "not_premium",
        ),
        (
            {"has_stories": False},
            {"toggles": {"with_stories": True, "exclude_admins": False}},
            "no_stories",
        ),
        (
            {"username": "x", "has_photo": True, "is_premium": True, "has_stories": True},
            {
                "toggles": {
                    "with_username": True,
                    "with_photo": True,
                    "premium_only": True,
                    "with_stories": True,
                    "exclude_admins": False,
                }
            },
            None,
        ),
    ],
)
def test_each_person_filter(
    user: dict[str, Any], settings: dict[str, Any], reason: str | None
) -> None:
    assert rejection(_agg(**user), _ctx(**settings)) == reason


@pytest.mark.parametrize(
    ("seen", "want", "kept"),
    [
        ("online", "recently", True),
        ("recently", "recently", True),
        ("week", "recently", False),
        ("week", "week", True),
        ("month", "week", False),
        ("month", "month", True),
        ("long", "month", False),
        ("long", "any", True),
        ("hidden", "month", False),
        ("hidden", "any", True),
    ],
)
def test_last_seen_buckets_and_hidden_only_under_any(seen: str, want: str, *, kept: bool) -> None:
    reason = rejection(_agg(last_seen=seen), _ctx(last_seen=want))
    assert (reason is None) is kept
    assert reason in {None, "last_seen"}


@pytest.mark.parametrize(
    ("mode", "count", "sources", "settings", "reason"),
    [
        ("messages", 1, ("@a",), {"min_messages": 2}, "min_messages"),
        ("messages", 2, ("@a",), {"min_messages": 2}, None),
        ("comments", 0, ("@a",), {}, "min_messages"),
        # Members are counted by being there: no message floor applies.
        ("members", 0, ("@a",), {"min_messages": 5}, None),
        ("members", 0, ("@a",), {"min_sources": 2, "sources": ["@a", "@b"]}, "min_sources"),
        ("messages", 3, ("@a", "@b"), {"min_sources": 2, "sources": ["@a", "@b"]}, None),
        # Seen somewhere but nothing that counted: no source on the list at all.
        ("messages", 0, (), {"min_messages": 1}, "min_messages"),
    ],
)
def test_activity_floors(
    mode: str, count: int, sources: tuple[str, ...], settings: dict[str, Any], reason: str | None
) -> None:
    agg = _agg(count=count, sources=sources)
    assert rejection(agg, _ctx(mode=mode, **settings)) == reason


def test_apply_filters_counts_each_reason_and_keeps_the_rest() -> None:
    people = [_agg(is_bot=True), _agg(is_bot=True), _agg(is_deleted=True), _agg()]

    kept, dropped = apply_filters(people, _ctx())

    assert kept == [people[3]]
    assert dropped == {"bot": 2, "deleted": 1}


def _message(text: str = "hello", **flags: bool) -> ChatHistoryMessage:
    return ChatHistoryMessage(message_id=1, sender_id=1, date=NOW, text=text, **flags)


@pytest.mark.parametrize(
    ("mode", "message", "settings", "counts"),
    [
        ("messages", _message(is_reply=True), {}, True),
        ("messages", _message(is_reply=True), {"toggles": {"include_replies": False}}, False),
        ("messages", _message(is_forward=True), {}, False),
        ("messages", _message(is_forward=True), {"toggles": {"include_forwards": True}}, True),
        # Under a channel post every comment is a reply: the toggles are the messages mode's.
        ("comments", _message(is_reply=True, is_forward=True), {}, True),
        ("comments", _message("  ok  "), {"limits": {"min_length": 3}}, False),
        ("comments", _message("long enough"), {"limits": {"min_length": 3}}, True),
        ("messages", _message("ab"), {"limits": {"min_length": 50}}, True),
        ("messages", _message("Selling a SHOP account"), {"keywords": ["shop"]}, True),
        ("messages", _message("Продаю Магазин"), {"keywords": ["нет", "магазин"]}, True),
        ("messages", _message("nothing here"), {"keywords": ["shop"]}, False),
    ],
)
def test_which_messages_count(
    mode: str, message: ChatHistoryMessage, settings: dict[str, Any], *, counts: bool
) -> None:
    assert message_counts(message, parser_request(mode=mode, **settings)) is counts


def test_the_collector_dedups_by_id_and_counts_only_what_counts() -> None:
    collector = Collector(["@a", "@b"])
    early, late = NOW - timedelta(days=2), NOW

    collector.message(person(1, first_name="Old"), 0, late, counts=True)
    collector.message(person(1, first_name="New"), 1, early, counts=True)
    collector.message(person(1), 1, NOW + timedelta(days=1), counts=False)
    collector.message(person(2), 0, late, counts=False)
    collector.member(person(3), 1)

    one, two, three = (collector.people[i] for i in (1, 2, 3))
    assert collector.raw == 3
    assert (one.message_count, one.first_at, one.last_at) == (2, early, late)
    assert list(one.sources) == ["@a", "@b"]
    assert (two.message_count, list(two.sources)) == (0, [])
    assert (three.message_count, list(three.sources)) == (0, ["@b"])
    assert (collector.source_count(0), collector.source_count(1)) == (2, 2)

    record = one.record()
    assert (record.first_name, record.message_count, record.sources) == ("U1", 2, ["@a", "@b"])
    assert record.first_at == early.isoformat()
    assert three.record().first_at is None
