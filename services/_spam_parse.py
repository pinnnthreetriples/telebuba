"""Reading @SpamBot's reply: the legacy classifier, the strict one, and redaction of samples.

Pure: no Telethon, no database, no clock. ``services.spam_status`` runs the probe and picks
between the two classifiers by ``settings.spam_intel.strict_parser``.

* **Legacy** is what shipped before the gateway. Its last step treats a reply no marker
  recognised, and that carries no date, as ``clean``. Its docstrings claimed German; there
  is no German marker.
* **Strict** keeps the same en/ru markers and the language-agnostic date rule, but a reply
  nothing recognised is ``unknown`` (``unrecognized_reply``) and a reply that matches both
  a clean and a limited marker is ``unknown`` (``ambiguous_reply``). It never reads
  ``getFullUser.restricted``: Telegram documents that flag as a platform restriction, not
  a spam limit.
* :func:`classify_transcript` reads what the human-like probe collected: several messages.

The order of a reading (docs/spam-intel.md 5.8): an error is ``unknown``; a structural read that
does not depend on the language (a date, in any form, means a time-bounded limit); the
per-language lexicons (``services._spam_lexicon``); anything else is unrecognised. "Limited" is
checked before "clean" across a transcript, and a text that matches both is ambiguous. A
multi-message transcript and markup on a message are RECORDED (the sample's structural flags) but
decide nothing yet: no captured reply shows what they mean.

Marker lexicons for more languages are added only from captured replies (``_spam_lexicon``).
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

from schemas.spam_status import SpamStatusKind, SpamStatusProbe
from services._spam_lexicon import (
    AUTOMATED_MARKERS,
    BEING_CHECKED_MARKERS,
    CLEAN_MARKERS,
    LIMITED_MARKERS,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

UNRECOGNIZED_DETAIL = "unrecognized_reply"
AMBIGUOUS_DETAIL = "ambiguous_reply"
CHECKING_DETAIL = "account is being checked"

# Date patterns — language-agnostic signal that the reply describes a time-bounded
# limitation ("until <date>"). A clean reply carries no date on any locale we have seen.
_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Numeric: 12.07.2026 / 12/07/2026 / 12-07-26 / 12.7.2026
    re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b"),
    # ISO: 2026-07-12
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b"),
    # Day + month-name + 4-digit year: "12 July 2026", "12. Juli 2026", "12 июля 2026",
    # "12 de julio de 2026". Month-name as any 3-9 letter word; the 4-digit year constraint
    # keeps the regex from grabbing arbitrary "<num> <word> <num>" sequences.
    re.compile(r"\b\d{1,2}\.?\s+(?:de\s+)?\w{3,9}\.?\s+(?:de\s+)?\d{4}\b", re.UNICODE),
)


def find_date(text: str) -> str | None:
    """Return the first date-like substring in ``text``, or ``None``."""
    for pat in _DATE_PATTERNS:
        match = pat.search(text)
        if match:
            return match.group(0)
    return None


def extract_until(text: str) -> str | None:
    """Pull the "until <date>" tail of a limited reply, falling back to any date.

    The English "until" prefix is the cleanest detail when present; for other locales the
    date itself is surfaced so the operator sees when the limitation expires.
    """
    idx = text.lower().find("until")
    if idx != -1:
        tail = text[idx:].splitlines()[0].strip().rstrip(".")
        if tail:
            return tail
    return find_date(text)


def _classify_reply_text(text: str) -> tuple[SpamStatusKind, str | None] | None:
    """Match the reply against the explicit keyword markers; ``None`` = none matched."""
    lowered = text.lower()
    if any(marker in lowered for marker in CLEAN_MARKERS):
        return "clean", None
    if any(marker in lowered for marker in BEING_CHECKED_MARKERS):
        return "unknown", CHECKING_DETAIL
    if any(marker in lowered for marker in LIMITED_MARKERS):
        return "limited", extract_until(text)
    return None


def _classify_by_heuristic(text: str) -> tuple[SpamStatusKind, str | None]:
    """Legacy verdict for a non-empty reply no marker matched.

    A date means limited, the ``automat`` stem means being-checked, and anything else is
    ``clean`` — the fallback the strict classifier removes.
    """
    date = find_date(text)
    if date is not None:
        return "limited", extract_until(text) or date
    lowered = text.lower()
    if any(marker in lowered for marker in AUTOMATED_MARKERS):
        return "unknown", CHECKING_DETAIL
    return "clean", None


def classify_legacy(probe: SpamStatusProbe) -> tuple[SpamStatusKind, str | None]:
    """The classifier as it was before the gateway.

    1. Probe error → unknown with the exception's CLASS NAME in ``detail``.
    2. Explicit keyword markers → precise verdict if any match.
    3. ``getFullUser.restricted`` flag → limited.
    4. Non-empty reply with no marker → heuristic (date → limited, ``automat`` → being
       checked, otherwise clean).
    5. Empty / no reply → unknown with no detail.
    """
    if probe.error:
        return "unknown", probe.error
    text = probe.reply_text or ""
    matched = _classify_reply_text(text)
    if matched is not None:
        return matched
    if probe.restricted:
        return "limited", probe.restriction_reason
    if text.strip():
        return _classify_by_heuristic(text)
    return "unknown", None


def classify_strict(  # noqa: PLR0911 - one flat ladder of verdicts, read top to bottom.
    probe: SpamStatusProbe,
) -> tuple[SpamStatusKind, str | None]:
    """The classifier that never answers ``clean`` for a text it did not recognise."""
    if probe.error:
        return "unknown", probe.error
    text = probe.reply_text or ""
    if not text.strip():
        return "unknown", None
    lowered = text.lower()
    clean = any(marker in lowered for marker in CLEAN_MARKERS)
    limited = any(marker in lowered for marker in LIMITED_MARKERS)
    if clean and limited:
        return "unknown", AMBIGUOUS_DETAIL
    if clean:
        return "clean", None
    if any(marker in lowered for marker in BEING_CHECKED_MARKERS):
        return "unknown", CHECKING_DETAIL
    if limited:
        return "limited", extract_until(text)
    date = find_date(text)
    if date is not None:
        return "limited", extract_until(text) or date
    if any(marker in lowered for marker in AUTOMATED_MARKERS):
        return "unknown", CHECKING_DETAIL
    return "unknown", UNRECOGNIZED_DETAIL


def classify_transcript(
    texts: Sequence[str],
    *,
    strict: bool,
) -> tuple[SpamStatusKind, str | None]:
    """The verdict for everything @SpamBot said in one conversation.

    Each message is read on its own, by the strict classifier when ``strict`` and by the legacy
    one otherwise, then the readings are combined: any ``limited`` message is ``limited`` (the
    first one's detail); else a ``clean`` message beside one that is ambiguous or says "being
    checked" is ``unknown`` (``ambiguous_reply``); else ``clean`` if any message is; else the most
    telling ``unknown`` (ambiguous, then being checked, then unrecognised). No text at all is
    ``unknown`` with no detail. ``getFullUser.restricted`` plays no part: the conversation does
    not read it.
    """
    classify = classify_strict if strict else classify_legacy
    readings = [
        classify(SpamStatusProbe(account_id="transcript", reply_text=text))
        for text in texts
        if text.strip()
    ]
    if not readings:
        return "unknown", None
    for reading in readings:
        if reading[0] == "limited":
            return reading
    unknown_details = {detail for status, detail in readings if status == "unknown"}
    unsettled = AMBIGUOUS_DETAIL in unknown_details or CHECKING_DETAIL in unknown_details
    if any(status == "clean" for status, _ in readings):
        return ("unknown", AMBIGUOUS_DETAIL) if unsettled else ("clean", None)
    for detail in (AMBIGUOUS_DETAIL, CHECKING_DETAIL):
        if detail in unknown_details:
            return "unknown", detail
    return readings[0]


# Redaction of stored samples: everything that names an account goes before a reply is kept.

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PLUS_PHONE = re.compile(r"\+\d[\d\s().-]{6,}\d")
_LONG_DIGITS = re.compile(r"\d{5,}")
_BLANK_LINES = re.compile(r"\n{3,}")
_SPACES = re.compile(r"[ \t]+")
_MIN_NAME_LENGTH = 2


def redact_reply(text: str, names: Iterable[str | None], max_chars: int) -> str:
    """The reply with everything that names an account removed, bounded to ``max_chars``.

    ``names``: the account's first name, last name, username and phone, as the account row
    has them. Each is replaced wherever it appears (case-insensitive, a ``@`` in front of a
    username included); e-mail addresses, ``+`` phone numbers and any run of five or more
    digits are masked. Dates survive: they carry no run of five digits and the parser's
    fixtures need them.
    """
    redacted = text.replace("\r\n", "\n")
    for name in sorted({n.strip() for n in names if n and n.strip()}, key=len, reverse=True):
        if len(name) >= _MIN_NAME_LENGTH:
            redacted = re.sub(rf"@?{re.escape(name)}", "<name>", redacted, flags=re.IGNORECASE)
    redacted = _EMAIL.sub("<email>", redacted)
    redacted = _PLUS_PHONE.sub("<phone>", redacted)
    redacted = _LONG_DIGITS.sub("<num>", redacted)
    redacted = _SPACES.sub(" ", redacted)
    redacted = _BLANK_LINES.sub("\n\n", redacted).strip()
    return redacted[:max_chars]


def reply_hash(redacted: str) -> str:
    """A stable key for a redacted reply: case and whitespace do not make a new sample."""
    normal = " ".join(redacted.lower().split())
    return hashlib.sha256(normal.encode()).hexdigest()[:32]
