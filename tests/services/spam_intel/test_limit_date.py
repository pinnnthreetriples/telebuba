"""The end of a dated limit, read from the date a reply names."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.spam_intel._limit_date import parse_limit_expiry


def _end(year: int, month: int, day: int) -> datetime:
    """Midnight after the named day, plus the one-hour hold."""
    return datetime(year, month, day + 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Your account is now limited until 29 Oct 2026.", _end(2026, 10, 29)),
        ("limited until October 29, 2026", _end(2026, 10, 29)),
        ("limited until Oct 29th, 2026", _end(2026, 10, 29)),
        ("ограничен до 29 октября 2026", _end(2026, 10, 29)),
        ("до 05.11.2026", _end(2026, 11, 5)),
        ("until 2026-11-05", _end(2026, 11, 5)),
        ("until 25/12/2026", _end(2026, 12, 25)),
        ("until 12/25/2026", _end(2026, 12, 25)),
        ("until 31 Dec 2026", datetime(2027, 1, 1, 1, tzinfo=UTC)),
    ],
)
def test_a_date_that_reads_one_way(text: str, expected: datetime) -> None:
    assert parse_limit_expiry(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "limited",
        "until 12/07/2026",  # day first or month first: not guessed
        "until 31 Feb 2026",
        "until 29 Foo 2026",  # a month name nobody has captured yet
        "30.02.2026",
        "peer_flood",
    ],
)
def test_anything_else_is_undated(text: str | None) -> None:
    assert parse_limit_expiry(text) is None
