"""Pure filter rules for channel discovery — no I/O, no state.

Two gates, because the facts arrive at two moments: ``admit_at_search`` sees only what
the search result carries (peer kind, public handle, the seen table), while
``admit_at_qualification`` sees what the ``getFullChannel`` probe learnt (comments,
join request, about text). Each returns the NAME of the filter that rejected — the run
report counts drops per filter — or ``None`` to admit.

Unknown values never reject: a filter can only refuse on a fact it actually has, so a
probe that failed to learn something leaves the row in rather than silently hiding it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from services.neurocomment._discovery_categories import matches

if TYPE_CHECKING:
    from schemas.neurocomment_discovery_request import DiscoverySearchRequest

_UKRAINIAN_MARKERS = frozenset("іїєґІЇЄҐ")


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


def access_of(username: str | None, join_request: bool | None) -> str:  # noqa: FBT001
    """``subscription`` (no public handle) / ``join_request`` / ``open``."""
    if not username or not username.strip():
        return "subscription"
    if join_request:
        return "join_request"
    return "open"


def admit_at_search(
    *,
    kind: str,
    access: str,
    ref: str,
    request: DiscoverySearchRequest,
    seen: set[str],
) -> str | None:
    """Reject on what a search hit already carries: ``kind``, ``access``, ``seen``.

    Only the ``subscription`` leg of the access filter is decidable here — whether a
    public channel needs a join request is learnt by the probe, so ``open`` versus
    ``join_request`` waits for ``admit_at_qualification``.
    """
    if (request.kind == "channels" and kind == "group") or (
        request.kind == "groups" and kind == "channel"
    ):
        return "kind"
    if request.access == "subscription" and access != "subscription":
        return "access"
    if request.access in {"open", "join_request"} and access == "subscription":
        return "access"
    if request.hide_seen and ref in seen:
        return "seen"
    return None


def admit_at_qualification(  # noqa: PLR0913 — one keyword per probed fact
    *,
    title: str,
    about: str | None,
    comments_enabled: bool | None,
    access: str | None,
    language: str | None,
    request: DiscoverySearchRequest,
) -> str | None:
    """Reject on what the probe learnt: ``comments``, ``access``, ``language``, ``category``.

    ``None`` for ``comments_enabled`` / ``access`` / ``language`` means the probe could
    not tell, and an unknown fact never rejects.
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
    if request.category != "any" and not matches(title, about, request.category):
        return "category"
    return None
