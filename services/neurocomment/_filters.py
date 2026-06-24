"""Post-filter — decide which fresh posts the engine comments on (pure, no I/O).

Split out of ``engine`` to keep that module within the file-size budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from services.content import has_link

if TYPE_CHECKING:
    from schemas.telegram_actions import NewPostEvent


def filter_reason(event: NewPostEvent) -> str | None:
    """Return why we skip this post, or ``None`` to proceed."""
    # getattr defense: if a NewPostEvent without is_forward ever reaches here
    # (e.g. a bad merge of the listener schema), degrade to "don't filter forwards"
    # rather than AttributeError-killing every post through the catch-all.
    if getattr(event, "is_forward", False):
        return "forward"
    text = event.text.strip()
    if event.has_media and not text:
        return "media_no_caption"
    if not text and not event.has_media:
        return "empty"
    if _is_link_only(event.text):
        return "link_only"
    return None


def _is_link_only(text: str) -> bool:
    """True when the text is essentially just a link / ad (few real word chars).

    Drops the link tokens themselves, then counts the remaining word characters —
    a post that is only a URL leaves almost nothing behind.
    """
    if not has_link(text):
        return False
    without_links = " ".join(token for token in text.split() if not has_link(token))
    stripped = "".join(ch for ch in without_links if ch.isalnum())
    return len(stripped) <= settings.neurocomment.link_only_max_word_chars
