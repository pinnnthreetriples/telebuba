"""Pure filter rules for channel discovery — no I/O, no state.

Two gates, because the facts arrive at two moments: ``admit_at_search`` sees only what
the search result carries (public handle, the seen table), while
``admit_at_qualification`` sees what the ``getFullChannel`` probe learnt (comments,
join request, about text). Each returns the NAME of the filter that rejected — the run
report counts drops per filter — or ``None`` to admit. ``kind`` is no gate here: every
source's gateway already applies it, so a hit of the wrong kind never arrives.

Unknown values never reject: a filter can only refuse on a fact it actually has, so a
probe that failed to learn something leaves the row in rather than silently hiding it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas.neurocomment_discovery_request import DiscoverySearchRequest

_UKRAINIAN_MARKERS = frozenset("іїєґІЇЄҐ")

# Rows stored under ``id:<n>``: a channel with no public handle, which nothing can probe
# and no campaign can comment in.
PRIVATE_PREFIX = "id:"


def private_ref(channel_id: int | None) -> str:
    """The stored form of a handle-less hit."""
    return f"{PRIVATE_PREFIX}{channel_id}"


def is_private_ref(ref: str) -> bool:
    return ref.startswith(PRIVATE_PREFIX)


def _is_cyrillic(char: str) -> bool:
    return "Ѐ" <= char <= "ӿ"


def _is_latin(char: str) -> bool:
    return char.isascii() or "À" <= char <= "ɏ"


def detect_language(text: str) -> str | None:
    """``ru`` / ``en`` / ``uk`` / ``other`` by letter script; ``None`` when no letters.

    Majority vote over letters only, so hashtags, digits and emoji do not sway it.
    Ukrainian is Cyrillic with any of the four letters Russian lacks (``іїєґ``).
    """
    letters = [char for char in text.strip() if char.isalpha()]
    if not letters:
        return None
    half = len(letters) / 2
    if sum(map(_is_cyrillic, letters)) > half:
        return "uk" if _UKRAINIAN_MARKERS.intersection(letters) else "ru"
    if sum(map(_is_latin, letters)) > half:
        return "en"
    return "other"


def access_of(username: str | None, join_request: bool | None) -> str | None:  # noqa: FBT001
    """``subscription`` (no public handle) / ``join_request`` / ``open`` / ``None``.

    ``None`` when the handle is public but nothing said whether joining needs approval:
    calling that ``open`` painted a badge the reply never earned, and let the
    ``join_request`` filter delete channels nobody had measured.
    """
    if not username or not username.strip():
        return "subscription"
    if join_request is None:
        return None
    return "join_request" if join_request else "open"


def admit_at_search(
    *,
    access: str | None,
    ref: str,
    request: DiscoverySearchRequest,
    seen: set[str],
) -> str | None:
    """Reject on what a search hit already carries: ``access``, ``seen``.

    Only the ``subscription`` leg of the access filter is decidable here — whether a
    public channel needs a join request is learnt by the probe, so ``open`` versus
    ``join_request`` waits for ``admit_at_qualification``. ``ref`` is the dedup key.
    """
    if request.access == "subscription" and access != "subscription":
        return "access"
    if request.access in {"open", "join_request"} and access == "subscription":
        return "access"
    if request.hide_seen and ref in seen:
        return "seen"
    return None


def admit_at_qualification(
    *,
    comments_enabled: bool | None,
    access: str | None,
    language: str | None,
    category_match: bool | None,
    request: DiscoverySearchRequest,
) -> str | None:
    """Reject on what the probe learnt: ``comments``, ``access``, ``language``, ``category``.

    Every fact arrives verdict-derived (``language`` off title + about, ``category_match``
    off the same text), so the filter reads exactly what the board will show. ``None``
    for any of them means the probe could not tell, and an unknown fact never rejects.
    A group has no comments verdict to speak of — comments ARE its messages, so
    ``comments_enabled`` is structurally false there — and the caller passes ``None`` for
    it rather than letting ``comments=on`` delete every group a ``kind=all`` search found.
    """
    if request.comments == "on" and comments_enabled is False:
        return "comments"
    if request.comments == "off" and comments_enabled is True:
        return "comments"
    wants_known_access = request.access in {"open", "join_request"} and access is not None
    if wants_known_access and access != request.access:
        return "access"
    if request.language != "any" and language is not None and language != request.language:
        return "language"
    if request.category != "any" and category_match is False:
        return "category"
    return None
