"""Post-filter — decide which fresh posts the engine comments on (pure, no I/O).

Split out of ``engine`` to keep that module within the file-size budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from services.content import has_link

if TYPE_CHECKING:
    from schemas.telegram_actions import NewPostEvent, PostMediaKind


def filter_reason(event: NewPostEvent) -> str | None:
    """Return why we skip this post, or ``None`` to proceed."""
    # getattr defense: if a NewPostEvent without is_forward ever reaches here
    # (e.g. a bad merge of the listener schema), degrade to "don't filter forwards"
    # rather than AttributeError-killing every post through the catch-all.
    if getattr(event, "is_forward", False):
        return "forward"
    text = event.text.strip()
    if not text:
        return _no_caption_reason(event.media_kind)
    if _is_link_only(event.text):
        return "link_only"
    return None


def _no_caption_reason(kind: PostMediaKind) -> str | None:
    """Why a post with no text is (still) not commentable — ``None`` for a readable photo.

    A caption-less photo is no longer dead weight: ``_generate`` downloads it and the
    model comments on what it sees. Everything else that arrives without a caption keeps
    its own reason, so the skip log prices what is genuinely still left on the table
    (``media_no_image``) apart from what an album never offers (``media_album_item`` — every
    item of an album lands in ONE discussion thread, so answering each would post three to
    five comments under a single visible post; we answer an album at most once, via its
    captioned head, and nothing here knows whether that head actually got a comment) and
    from the operator's own off-switch
    (``media_no_caption``, the pre-vision behaviour, when the size cap is set to 0).
    """
    if kind == "none":
        return "empty"
    if kind == "album":
        return "media_album_item"
    if kind != "photo":
        return "media_no_image"
    return None if settings.neurocomment.vision_max_image_bytes > 0 else "media_no_caption"


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
