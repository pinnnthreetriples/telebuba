"""Which spam events reach the operator bot, and the two facts their message names.

Split out of ``_notification_messages`` for its size. The ``spam_limited`` notification
(design 5.12, owner decision D16) names the account, the day the limit ends or "бессрочно", and
where the news came from. Both facts are DERIVED here from values the program computed: the day is
an ISO date the evidence layer wrote (or one parsed out of a reply's date by the same code), the
source comes from a fixed map of evidence kinds. A reply's own words, a log row's ``detail`` and
any other ``extra`` never reach the message.

News only, per account: a verdict that was already limited, or that is only ending
(``limited`` -> ``unknown``, a dated limit running out silently), sends nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from services.spam_intel import limit_end_date, limit_has_ended

if TYPE_CHECKING:
    from schemas.logs import LogEntry
    from schemas.notifications import NotificationKind

SPAM_STATUS_EVENT = "spam_status_refreshed"
VERDICT_EVENT = "spam_intel_verdict_changed"
SESSION_DEAD_EVENT = "account_session_dead"
# Events whose notification is ``spam_kind`` alone: when it says nothing, they send nothing.
SPAM_ONLY_EVENTS = frozenset({SPAM_STATUS_EVENT, VERDICT_EVENT})

# The evidence kinds whose news the operator hears, and how the message names the source. A
# chat-ban refusal (``user_banned_in_channel``) is absent on purpose: its scope is not known to be
# the account, so it moves the fold but does not ping.
SOURCE_NAMES: dict[str, str] = {
    "spambot_limited": "@SpamBot",
    "peer_flood_send": "PeerFlood при отправке",
    "peer_flood_other": "PeerFlood",
}
SPAMBOT_NAME = SOURCE_NAMES["spambot_limited"]
# PeerFlood during warming already sends the ``quarantine`` notification.
_PEER_FLOOD_KINDS = frozenset({"peer_flood_send", "peer_flood_other"})


@dataclass(frozen=True)
class SpamNotice:
    """What a spam message names besides the account: the end day (``None`` = open-ended)."""

    until: date | None
    source: str | None


def _day(value: object) -> date | None:
    """An ISO day the program wrote; anything else is no date."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _moment(entry: LogEntry) -> datetime:
    """When the row was written (an unreadable stamp: now, the row is handled as it arrives)."""
    try:
        at = datetime.fromisoformat(entry.created_at)
    except ValueError:
        return datetime.now(UTC)
    return at if at.tzinfo is not None else at.replace(tzinfo=UTC)


def _was_limited(entry: LogEntry) -> bool:
    """The reading before this one was a limit still on (not one whose date had passed)."""
    extra = entry.extra
    if extra.get("previous") != "limited":
        return False
    detail = extra.get("previous_detail")
    return not (isinstance(detail, str) and limit_has_ended(detail, _moment(entry)))


def _status_notice(entry: LogEntry) -> SpamNotice | None:
    """A real @SpamBot reading that is news: limited, and not limited before.

    "Before" is the limit as it stood at this reading: a previous limit whose date had passed
    was over, so a new limit after it is news and not a repeat.
    """
    extra = entry.extra
    if extra.get("status") != "limited" or _was_limited(entry):
        return None
    detail = extra.get("detail")
    return SpamNotice(
        until=limit_end_date(detail if isinstance(detail, str) else None), source=SPAMBOT_NAME
    )


def _verdict_notice(entry: LogEntry) -> SpamNotice | None:
    """A fold that turned limited, from evidence the operator should hear about."""
    extra = entry.extra
    if extra.get("to") != "limited" or extra.get("from") == "limited":
        return None
    source = SOURCE_NAMES.get(str(extra.get("primary_kind")))
    if source is None:
        return None
    return SpamNotice(until=_day(extra.get("until")), source=source)


def limited_notice(entry: LogEntry) -> SpamNotice | None:
    """The ``spam_limited`` news an entry carries, or ``None`` when it carries none."""
    if entry.event == SPAM_STATUS_EVENT:
        return _status_notice(entry)
    if entry.event == VERDICT_EVENT:
        return _verdict_notice(entry)
    return None


def is_frozen_news(entry: LogEntry) -> bool:
    """A freeze that is news: the fold turned frozen, or the session check found the freeze."""
    extra = entry.extra
    if entry.event == VERDICT_EVENT:
        return extra.get("to") == "frozen" and extra.get("from") != "frozen"
    return entry.event == SESSION_DEAD_EVENT and extra.get("status") == "frozen"


def is_passive_peer_flood(entry: LogEntry) -> bool:
    """A limit the fold took from PeerFlood evidence rather than from an @SpamBot answer."""
    return entry.event == VERDICT_EVENT and entry.extra.get("primary_kind") in _PEER_FLOOD_KINDS


def spam_kind(entry: LogEntry) -> NotificationKind | None:
    """``spam_limited`` or ``account_frozen`` for an entry that is that news, else ``None``."""
    if is_frozen_news(entry):
        return "account_frozen"
    return "spam_limited" if limited_notice(entry) is not None else None
