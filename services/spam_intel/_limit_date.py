"""When a dated @SpamBot limit ends, as an aware UTC instant, from the date in the reply.

Pure. Understands what is unambiguous: ISO (``2026-10-29``), ``29.10.2026``, a day and an
English or Russian month name (``29 Oct 2026``, ``29 октября 2026``, ``Oct 29, 2026``), and
``/`` dates when only one reading is possible. Anything else is ``None``: an undated limit
does not expire by itself and is rechecked after ``limited_recheck_days`` instead, which is the
safe side. Month names of other languages are added only from captured replies (the same rule
as the reply lexicons, docs/spam-intel.md).

A date names a DAY, so the limit is taken to hold through that day and one hour past midnight
(design 5.3: "the date in the reply, +1 h"); ending a day early would let a limited account
look free.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

_MONTHS = {
    **{
        name: number
        for number, names in enumerate(
            (
                ("january", "jan"),
                ("february", "feb"),
                ("march", "mar"),
                ("april", "apr"),
                ("may",),
                ("june", "jun"),
                ("july", "jul"),
                ("august", "aug"),
                ("september", "sep", "sept"),
                ("october", "oct"),
                ("november", "nov"),
                ("december", "dec"),
            ),
            start=1,
        )
        for name in names
    },
    **{
        name: number
        for number, name in enumerate(
            (
                "января",
                "февраля",
                "марта",
                "апреля",
                "мая",
                "июня",
                "июля",
                "августа",
                "сентября",
                "октября",
                "ноября",
                "декабря",
            ),
            start=1,
        )
    },
}

_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DOTTED = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_SLASHED = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_DAY_MONTH = re.compile(r"\b(\d{1,2})\.?\s+([^\W\d_]{3,9})\.?,?\s+(\d{4})\b")
_MONTH_DAY = re.compile(r"\b([^\W\d_]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
HOLD = timedelta(days=1, hours=1)
_MAX_MONTH = 12


def _day(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _named(text: str) -> date | None:
    for match in _DAY_MONTH.finditer(text):
        month = _MONTHS.get(match.group(2).lower())
        if month is not None:
            return _day(int(match.group(3)), month, int(match.group(1)))
    for match in _MONTH_DAY.finditer(text):
        month = _MONTHS.get(match.group(1).lower())
        if month is not None:
            return _day(int(match.group(3)), month, int(match.group(2)))
    return None


def _slashed(text: str) -> date | None:
    match = _SLASHED.search(text)
    if match is None:
        return None
    first, second, year = (int(part) for part in match.groups())
    if first > _MAX_MONTH >= second:
        return _day(year, second, first)
    if second > _MAX_MONTH >= first:
        return _day(year, first, second)
    return None  # ``12/07/2026`` reads two ways: not guessed


def _date_in(text: str) -> date | None:
    if (match := _ISO.search(text)) is not None:
        return _day(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if (match := _DOTTED.search(text)) is not None:
        return _day(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    return _named(text) or _slashed(text)


def parse_limit_expiry(text: str | None) -> datetime | None:
    """The instant a limit named by ``text`` is taken to end, or ``None`` when undated."""
    if not text:
        return None
    named = _date_in(text)
    if named is None:
        return None
    return datetime(named.year, named.month, named.day, tzinfo=UTC) + HOLD


def end_date(expiry: datetime | None) -> date | None:
    """The calendar day a limit ending at ``expiry`` was named for (``expiry`` minus the hold)."""
    return None if expiry is None else (expiry - HOLD).date()


def limit_end_date(text: str | None) -> date | None:
    """The day a reply says a limit ends, or ``None`` when it names none."""
    return end_date(parse_limit_expiry(text))


def limit_has_ended(text: str | None, at: datetime) -> bool:
    """A dated limit named by ``text`` has run out by ``at`` (an undated one never does)."""
    expiry = parse_limit_expiry(text)
    return expiry is not None and at >= expiry
