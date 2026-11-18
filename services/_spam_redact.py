"""Redaction of a stored @SpamBot reply: everything that names a person goes before it is kept.

Pure: no database, no clock. ``services.spam_status`` redacts a reply here, and only the redacted
text is ever written to ``spam_reply_samples``. The rule is conservative on purpose: a mask that
costs a word of a fixture is cheap, a number or a handle that stays is not.

Masked, wherever they stand: links (``http(s)://``, ``tg://``, ``t.me/...``), e-mail addresses,
``@usernames`` of anyone (but the bot's own ``@SpamBot``), every name of the account (any length:
a name of up to three characters only as a whole token, so ``Al`` does not eat ``alarm``; each
word of a composite name on its own), phone numbers in any common format (digits with spaces,
dashes, dots, parentheses or a leading ``+``) and any run of five or more digits. A date survives
(``12.07.2026``, ``2026-07-12``): it names no one, and the parser's fixtures need it.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_LINK = re.compile(
    r"(?:https?://|tg://|www\.)\S+|\b(?:t|telegram)\.(?:me|dog)/\S+",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_USERNAME = re.compile(r"@(?!spambot\b)\w+", re.IGNORECASE)
# A phone is a digit run broken by up to two separators at a time; it counts from this many
# digits (six with a leading ``+``). A date never reaches it: dates are cut out first.
_PHONE = re.compile(r"\+?\(?\d(?:[ \t().\-]{0,2}\d)+")
_PHONE_MIN_DIGITS = 7
_PLUS_PHONE_MIN_DIGITS = 6
_LONG_RUN = 5
_LONG_DIGITS = re.compile(r"\d{5,}")  # the net under the phone pass: whatever it left
# A date as a whole: neither side may continue into another digit group, or ``06-12-34-56-78``
# would keep its first three groups as a "date".
_SAFE_DATE = re.compile(
    r"(?<!\d)(?<!\d[./-])"
    r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4})"
    r"(?!\d)(?![./-]\d)"
)
_BLANK_LINES = re.compile(r"\n{3,}")
_SPACES = re.compile(r"[ \t]+")
_NAME_WORDS = re.compile(r"[^\w]+")
_WHOLE_TOKEN_MAX = 3


def _mask_phone(match: re.Match[str]) -> str:
    text = match.group(0)
    digits = sum(ch.isdigit() for ch in text)
    if text.lstrip("+").isdigit():  # no separator at all: an id or a number run, not a format
        return "<num>" if digits >= _LONG_RUN else text
    needed = _PLUS_PHONE_MIN_DIGITS if text.startswith("+") else _PHONE_MIN_DIGITS
    return "<phone>" if digits >= needed else text


def _mask_numbers(segment: str) -> str:
    return _LONG_DIGITS.sub("<num>", _PHONE.sub(_mask_phone, segment))


def _mask_numbers_outside_dates(text: str) -> str:
    out: list[str] = []
    last = 0
    for date in _SAFE_DATE.finditer(text):
        out.append(_mask_numbers(text[last : date.start()]))
        out.append(date.group(0))
        last = date.end()
    out.append(_mask_numbers(text[last:]))
    return "".join(out)


def _name_tokens(names: Iterable[str | None]) -> list[str]:
    """Every name of the account and every word of it, longest first."""
    tokens: set[str] = set()
    for name in names:
        if not name or not name.strip():
            continue
        tokens.add(name.strip())
        tokens.update(word for word in _NAME_WORDS.split(name) if word)
    return sorted((t for t in tokens if any(ch.isalnum() for ch in t)), key=len, reverse=True)


def _mask_names(text: str, names: Iterable[str | None]) -> str:
    for token in _name_tokens(names):
        escaped = re.escape(token)
        pattern = rf"(?<!\w){escaped}(?!\w)" if len(token) <= _WHOLE_TOKEN_MAX else escaped
        text = re.sub(pattern, "<name>", text, flags=re.IGNORECASE)
    return text


def redact_reply(text: str, names: Iterable[str | None], max_chars: int) -> str:
    """The reply with everything that names a person removed, bounded to ``max_chars``.

    ``names``: the account's first name, last name, username and phone, as the account row has
    them. See the module docstring for what is masked.
    """
    redacted = text.replace("\r\n", "\n")
    redacted = _LINK.sub("<link>", redacted)
    redacted = _EMAIL.sub("<email>", redacted)
    redacted = _mask_names(redacted, names)
    redacted = _USERNAME.sub("<username>", redacted)
    redacted = _mask_numbers_outside_dates(redacted)
    redacted = _SPACES.sub(" ", redacted)
    redacted = _BLANK_LINES.sub("\n\n", redacted).strip()
    return redacted[:max_chars]


def reply_hash(redacted: str) -> str:
    """A stable key for a redacted reply: case and whitespace do not make a new sample."""
    normal = " ".join(redacted.lower().split())
    return hashlib.sha256(normal.encode()).hexdigest()[:32]
