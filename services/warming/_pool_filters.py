"""What a found channel must look like to go into the warming pool.

One set of checks for every search — «Найти каналы», the automatic refill and a warming
account looking for itself — so a channel is never good enough for one and not another:
- between ``pool_fill_min_subscribers`` and ``pool_fill_max_subscribers`` subscribers;
- posted within ``pool_fill_alive_days``;
- not visibly written in another language;
- no blocked word (scam, 18+, casino, betting, crypto signals) at the start of a word in
  its title or about text, and not marked scam or fake by Telegram itself;
- a public handle that joins at once, not through a join request.

Pure: the probe reads that answer the size and the last post live in ``_pool_hunt``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING

from core.channel_tokens import dedup_key
from core.config import settings
from services._lang_detect import detect_language

if TYPE_CHECKING:
    from schemas.telegram_actions_discovery import TelegramChannelMatch


@lru_cache(maxsize=4)
def _blocked_pattern(words: tuple[str, ...]) -> re.Pattern[str] | None:
    """One regex for the whole list: each word anchored to the start of a word."""
    stems = [re.escape(word.casefold()) for word in words if word.strip()]
    if not stems:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(stems) + ")")


def is_blocked(text: str) -> bool:
    """Whether a word of ``text`` starts with a blocked word (case-insensitive)."""
    pattern = _blocked_pattern(tuple(settings.warming.pool_fill_blocked_words))
    return pattern is not None and pattern.search(text.casefold()) is not None


def size_ok(size: int | None) -> bool:
    warm = settings.warming
    return (
        size is not None
        and warm.pool_fill_min_subscribers <= size <= warm.pool_fill_max_subscribers
    )


def text_ok(text: str, language: str) -> bool:
    """No blocked word, and not visibly written in another language."""
    return not is_blocked(text) and detect_language(text) in {None, language}


def is_candidate(match: TelegramChannelMatch, language: str, known: set[str]) -> bool:
    """The free checks, before any probe: a new public handle, the title, a size if one came."""
    if not match.username or dedup_key(match.username) in known:
        return False
    if match.flagged or match.join_request:
        return False
    size = match.participants_count
    return text_ok(match.title, language) and (size is None or size_ok(size))


def is_alive(last_post_at: str | None, now: datetime) -> bool:
    """Whether the newest post is recent enough for a channel people still read."""
    if last_post_at is None:
        return False
    posted = datetime.fromisoformat(last_post_at)
    posted = posted if posted.tzinfo is not None else posted.replace(tzinfo=UTC)
    return now - posted <= timedelta(days=settings.warming.pool_fill_alive_days)
