"""Pure parsing of Telemetr.io's wire shapes — no HTTP, no config, no state.

Split out of ``core.telemetr`` when that module outgrew the 400-line gate. The seam is a
real one: nothing here opens a socket or reads settings, so the catalogue contract can be
exercised against documented payloads alone.
"""

from __future__ import annotations

from typing import NamedTuple, cast

from core.channel_tokens import normalize_channel
from schemas.telemetr import (
    TELEMETR_MAX_MEMBERS,
    TELEMETR_MAX_TITLE_LENGTH,
    TelemetrSearchResult,
    TelemetrStatus,
)

# Telegram's own username ceiling, which is what a resolved handle must fit.
_HANDLE_MAX_LENGTH = 32
# The dropdown values the UI offers (``COUNTRIES`` in DiscoveryForm.tsx) bridged to the
# english names the dictionary files them under. A name Telemetr spells differently
# surfaces as ``unresolved_filter``, which is loud — never a silently empty result.
# Only countries need this: a language ``id`` already IS the ISO-639-1 code the form
# sends, so fetching that dictionary would spend a billable request to map "tr" to "tr".
_COUNTRY_NAME_BY_ALPHA2 = {
    "RU": "Russia",
    "KZ": "Kazakhstan",
    "UZ": "Uzbekistan",
    "UA": "Ukraine",
    "BY": "Belarus",
    "DE": "Germany",
    "FR": "France",
    "ES": "Spain",
    "GB": "United Kingdom",
    "TR": "Turkey",
    "AE": "United Arab Emirates",
    "SA": "Saudi Arabia",
    "EG": "Egypt",
}


class _TelemetrError(Exception):
    """Internal signal carrying the typed result ``search_catalog`` will return."""

    def __init__(self, *, status: TelemetrStatus, error: str) -> None:
        super().__init__(error)
        self.result = TelemetrSearchResult(status=status, error=error)


class _CatalogRow(NamedTuple):
    """A parsed ``CatalogItem``: everything but the handle, which it does not carry."""

    internal_id: str
    title: str
    members_count: int | None
    country: str | None
    language: str | None


def _bounded_count(value: object) -> int | None:
    """Return a persistable count, or ``None`` when the row's is unusable.

    ``isinstance(True, int)`` holds in Python, so a JSON ``true`` would otherwise
    become one subscriber and quietly fail a ``members_min`` filter. An out-of-range
    count is unknown rather than fatal: the channel is still a usable candidate, and
    the alternative is losing the whole run's candidate write.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    if not 0 <= value <= TELEMETR_MAX_MEMBERS:
        return None
    return value


def _bounded_text(value: object) -> str | None:
    """Return a bounded tag, or ``None`` when the row has none.

    Bounded for the same reason as the title: an unbounded third-party string rides
    through persistence into every SPA board poll of the run.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:TELEMETR_MAX_TITLE_LENGTH]


def _parse_row(entry: object) -> _CatalogRow | None:
    if not isinstance(entry, dict):
        return None
    row = cast("dict[str, object]", entry)
    # ``internal_id`` is the only key into /channels/info-batch, which is where the
    # handle comes from — a row without one can never become a candidate.
    internal_id = row.get("internal_id")
    if not isinstance(internal_id, str) or not internal_id.strip():
        return None
    title = row.get("title")
    # Truncate instead of dropping the row or raising: an over-long title is a
    # cosmetic defect, but an unbounded one is a board payload we re-serialise on
    # every SPA poll.
    return _CatalogRow(
        internal_id=internal_id.strip(),
        title=title.strip()[:TELEMETR_MAX_TITLE_LENGTH] if isinstance(title, str) else "",
        members_count=_bounded_count(row.get("members_count")),
        country=_bounded_text(row.get("country")),
        language=_bounded_text(row.get("language")),
    )


def _extract_items(body: object, limit: int) -> tuple[list[_CatalogRow], int | None]:
    """Read the ``{items, count, audience_count}`` envelope into rows plus the total.

    Tolerates a bare array too rather than failing the whole source on an envelope
    change; such a body simply carries no total.
    """
    rows: object = body
    total: object = None
    if isinstance(body, dict):
        envelope = cast("dict[str, object]", body)
        rows = envelope.get("items")
        total = envelope.get("count")
    if not isinstance(rows, list):
        return [], None
    # ``limit`` on the wire is a request, not a promise: parse no more than was asked
    # for, so a long body cannot inflate the run's candidate set.
    parsed = (_parse_row(entry) for entry in rows[:limit])
    return [row for row in parsed if row is not None], _bounded_count(total)


def _handle_from_link(link: object) -> str | None:
    """Derive the public @handle from ``ChatInfo.link``, the API's only handle field.

    Delegated to ``core.channel_tokens`` — same layer, and it already owns every accepted
    spelling: both schemes, ``t.me/``, ``telegram.me/``, a query string, a trailing slash,
    invite hashes.
    """
    if not isinstance(link, str):
        return None
    handle = normalize_channel(link, max_length=_HANDLE_MAX_LENGTH)
    # A "+…" key is a private invite: there is no public handle to comment under.
    if handle is None or handle.startswith("+"):
        return None
    return handle


def _handles_from_batch(body: object) -> dict[str, str] | None:
    """Map ``internal_id`` to public handle for the rows that have one.

    Drops what a campaign cannot comment under: a group, a row without a link, and a
    private invite. An id the API omits from ``channels`` is absent here too.

    ``None`` means the reply had no ``channels`` list at all — a contract change, which is
    a different thing from a page whose every row was legitimately dropped.
    """
    channels: object = body
    if isinstance(body, dict):
        channels = cast("dict[str, object]", body).get("channels")
    if not isinstance(channels, list):
        return None
    handles: dict[str, str] = {}
    for entry in channels:
        if not isinstance(entry, dict):
            continue
        info = cast("dict[str, object]", entry)
        internal_id = info.get("internal_id")
        # ``peer`` is a chat-type discriminator ("Group"/"Channel"), never a handle.
        if not isinstance(internal_id, str) or info.get("peer") != "Channel":
            continue
        handle = _handle_from_link(info.get("link"))
        if handle is not None:
            handles[internal_id.strip()] = handle
    return handles


def _resolved_country(value: str, lookup: dict[str, str]) -> str:
    identifier = lookup.get(value.casefold())
    if identifier is None:
        # The operator picks an ISO-3166 alpha-2 code, which matches neither an id
        # ("turkey") nor a name ("Turkey"); bridge it through the english name.
        english = _COUNTRY_NAME_BY_ALPHA2.get(value.upper())
        identifier = lookup.get(english.casefold()) if english is not None else None
    if identifier is None:
        raise _TelemetrError(
            status="unresolved_filter",
            error=f"Unknown country filter: {value!r}",
        )
    return identifier
