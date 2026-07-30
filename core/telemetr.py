"""Telemetr.io channel-catalogue gateway — the external half of channel discovery.

The only module that talks HTTP to Telemetr.io. Mirrors ``core.gemini`` /
``core.openai``: the service passes a typed :class:`TelemetrSearchRequest` and
gets a :class:`TelemetrSearchResult` back — never an exception, so one flaky
external source can never abort a discovery run.

One :func:`search_catalog` call spends up to three requests:

* ``GET /dictionaries/{countries,languages}`` — only when the operator set that
  filter, and only once per process: the reference data is static, so it is
  cached. A country ``id`` is a slug ("turkey"), not an ISO-3166 code, so the
  operator's value has to be resolved against the dictionary before it is sent.
* ``GET /catalog/search`` with ``search_in_about`` pinned on: matching the
  description as well as the title roughly doubles recall for a topical keyword,
  which is exactly what discovery wants.
* ``GET /channels/info-batch`` — one request per <=100 catalogue rows. A
  ``CatalogItem`` carries no handle at all, and every consumer downstream needs
  one (campaign channels are handles), so the collected ``internal_id`` values are
  resolved to ``link`` here. It doubles the per-keyword cost against a free tier
  of 1000 requests/month, and it is not optional: without it the catalogue yields
  nothing usable.

An empty key short-circuits to ``status="not_configured"`` before any socket is
opened — a skipped source, not a failure.
"""

from __future__ import annotations

import asyncio
from typing import NamedTuple, cast

import httpx

from core.config import settings
from schemas.telemetr import (
    TELEMETR_MAX_MEMBERS,
    TELEMETR_MAX_TITLE_LENGTH,
    TelemetrChannel,
    TelemetrSearchRequest,
    TelemetrSearchResult,
    TelemetrStatus,
)

_HTTP_OK = 200
_HTTP_SERVER_ERROR_MIN = 500
# A second attempt cannot change any of these answers — except 429, the one code a
# backoff genuinely fixes, so it is retried (see ``_retryable``) while still reporting
# its own status. For the two quota-shaped ones a retry would spend another billable
# unit against a limit that is already gone. Documented for /catalog/search; 429 is kept
# as belt and braces even though the API does not document it.
_HTTP_TOO_MANY_REQUESTS = 429
_STATUS_BY_CODE: dict[int, TelemetrStatus] = {
    400: "bad_request",
    401: "auth_failed",
    403: "forbidden",
    404: "not_found",
    412: "subscription_inactive",  # Inactive Subscription
    426: "quota_exhausted",  # Quota Reached
    429: "rate_limited",
}
# /channels/info-batch accepts at most 100 comma-separated ids per request.
_INFO_BATCH_MAX_IDS = 100
# Enough of an upstream error body to diagnose one, short enough to persist in a log.
_BODY_EXCERPT_MAX = 200
_DICTIONARY_PATHS = {"country": "/dictionaries/countries", "language": "/dictionaries/languages"}
# The dropdown values the UI offers (``COUNTRIES`` in DiscoveryForm.tsx) bridged to the
# english names the dictionary files them under. A language ``id`` already *is* an
# ISO-639-1 code, so only countries need the bridge. A name Telemetr spells differently
# surfaces as ``unresolved_filter``, which is loud — never a silently empty result.
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
# Deterministic construction failures, caught before ``HTTPError`` because two of them
# subclass it: an operator key with a non-breaking space or a Cyrillic character never
# encodes (``UnicodeError``), a key with a newline never becomes a legal header
# (``LocalProtocolError``), and a ``TELEMETR__BASE_URL`` that is malformed or missing
# its scheme never resolves (``InvalidURL``, ``UnsupportedProtocol``). A second attempt
# cannot change any of them, so retrying would only spend a request and delay the
# honest error.
_DETERMINISTIC_FAILURES = (
    httpx.InvalidURL,
    httpx.UnsupportedProtocol,
    httpx.LocalProtocolError,
    UnicodeError,
)

# The countries/languages dictionaries are static reference data, so one fetch per
# process is enough — no TTL, no invalidation.
_dictionary_cache: dict[str, dict[str, str]] = {}
# One lock per dictionary, because a run fires every keyword's catalogue query at once and
# they all miss the cache together: 10 keywords with both filters cost 20 dictionary GETs
# instead of 2, against a 1000/month tier. Created lazily inside the running loop — a
# module-level Lock() would bind whichever loop imported this module first.
_dictionary_locks: dict[str, asyncio.Lock] = {}


class _ClientHolder:
    client: httpx.AsyncClient | None = None


_holder = _ClientHolder()


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


def _get_client() -> httpx.AsyncClient:
    """Return the shared AsyncClient, creating it on first use (reused across calls)."""
    if _holder.client is None:
        _holder.client = httpx.AsyncClient(timeout=settings.telemetr.timeout_seconds)
    return _holder.client


async def close_telemetr_client() -> None:
    """Close the shared AsyncClient and drop the dictionary cache.

    Called from the app lifespan on shutdown, so the cache lives exactly as long as the
    client it was fetched with.
    """
    if _holder.client is not None:
        await _holder.client.aclose()
        _holder.client = None
    _dictionary_cache.clear()
    # The locks are bound to the loop that created them, so they must not outlive it.
    _dictionary_locks.clear()


def _url(path: str) -> str:
    return f"{settings.telemetr.base_url}{path}"


def _error_text(exc: BaseException) -> str:
    """Name a transport failure without echoing a detail that is the API key.

    h11 renders an illegal header value by quoting it verbatim, and the illegal value
    is the key — so a ``LocalProtocolError`` would put the key straight into the run's
    error string, which the caller shows the operator and persists. A key that will not
    encode leaks the offending character the same way.
    """
    if isinstance(exc, httpx.LocalProtocolError):
        return "LocalProtocolError: the API key is not a legal header value"
    if isinstance(exc, UnicodeError):
        return f"{type(exc).__name__}: the API key is not encodable as a header"
    return f"{type(exc).__name__}: {exc}"


def _status_for(status_code: int) -> TelemetrStatus:
    return _STATUS_BY_CODE.get(status_code, "error")


def _retryable(status_code: int) -> bool:
    """A 5xx or a rate limit — the only replies a second attempt can turn into a 200."""
    return status_code >= _HTTP_SERVER_ERROR_MIN or status_code == _HTTP_TOO_MANY_REQUESTS


def _params(request: TelemetrSearchRequest, filters: dict[str, str]) -> dict[str, str | int]:
    params: dict[str, str | int] = {
        "term": request.term,
        # Widen recall: a topical keyword usually appears in the description too.
        "search_in_about": "true",
        "limit": request.limit,
    }
    optional: dict[str, str | int | None] = {
        **filters,
        "members_min": request.members_min,
        "members_max": request.members_max,
    }
    # A blank value is not a filter: it would reach the wire as a bare ``?country=``.
    params.update({key: value for key, value in optional.items() if value not in (None, "")})
    return params


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
    """Derive the public @handle from ``ChatInfo.link``, the API's only handle field."""
    if not isinstance(link, str):
        return None
    handle = link.strip()
    for scheme in ("https://", "http://"):
        if handle.lower().startswith(scheme):
            handle = handle[len(scheme) :]
    if handle.lower().startswith("t.me/"):
        handle = handle[len("t.me/") :]
    # A post link ("t.me/chan/42") still names the channel in its first segment.
    handle = handle.split("/", 1)[0].lstrip("@").strip()
    # A "+…" link is a private invite: there is no public handle to comment under.
    if not handle or handle.startswith("+"):
        return None
    return handle


def _handles_from_batch(body: object) -> dict[str, str]:
    """Map ``internal_id`` to public handle for the rows that have one.

    Drops what a campaign cannot comment under: a group, a row without a link, and a
    private invite. An id the API omits from ``channels`` is absent here too.
    """
    channels: object = body
    if isinstance(body, dict):
        channels = cast("dict[str, object]", body).get("channels")
    if not isinstance(channels, list):
        return {}
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


async def _get_json(api_key: str, path: str, params: dict[str, str] | None = None) -> object:
    """GET a supporting endpoint, raising ``_TelemetrError`` on anything but a 200.

    Unretried on purpose: a dictionary or batch failure is reported so the operator
    sees it, and a retry would spend quota on a run that already needs restarting.
    """
    try:
        response = await _get_client().get(
            _url(path),
            headers={"x-api-key": api_key},
            params=params,
        )
    except (*_DETERMINISTIC_FAILURES, httpx.HTTPError) as exc:
        raise _TelemetrError(status="error", error=_error_text(exc)) from exc
    if response.status_code != _HTTP_OK:
        raise _TelemetrError(
            status=_status_for(response.status_code),
            error=f"HTTP {response.status_code} on {path}: {_body_excerpt(response, api_key)}",
        )
    return _parse_body(response)


def _body_excerpt(response: httpx.Response, api_key: str) -> str:
    """An upstream error body, safe to show the operator and to persist.

    The statuses that carry a body here are the credential ones (401/403/412/426), and
    gateways routinely quote the presented key back in them. Scrub BEFORE truncating:
    cutting to 200 characters first can split the key and leave a fragment that no
    later replace would match.
    """
    return response.text.replace(api_key, "***")[:_BODY_EXCERPT_MAX] if api_key else ""


def _parse_body(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError as exc:
        raise _TelemetrError(status="error", error=f"Invalid JSON: {exc}") from exc


async def _dictionary(api_key: str, kind: str) -> dict[str, str]:
    """Return a casefolded ``{id or name -> id}`` lookup for one dictionary endpoint.

    Fetched at most once per process: the concurrent callers queue on the lock and the
    re-check inside it serves them from the cache.
    """
    cached = _dictionary_cache.get(kind)
    if cached is not None:
        return cached
    async with _dictionary_locks.setdefault(kind, asyncio.Lock()):
        cached = _dictionary_cache.get(kind)
        if cached is not None:
            return cached
        return await _load_dictionary(api_key, kind)


async def _load_dictionary(api_key: str, kind: str) -> dict[str, str]:
    body = await _get_json(api_key, _DICTIONARY_PATHS[kind])
    # Documented shape is a bare array of {id, name, channels_count, participants_count}.
    if not isinstance(body, list):
        raise _TelemetrError(status="error", error=f"Unreadable {kind} dictionary")
    entries: list[tuple[str, str | None]] = []
    for item in cast("list[object]", body):
        if not isinstance(item, dict):
            continue
        row = cast("dict[str, object]", item)
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            continue
        label = row.get("name")
        entries.append((identifier.strip(), label.strip() if isinstance(label, str) else None))
    # Ids are applied last so a name can never shadow another entry's id.
    lookup = {label.casefold(): identifier for identifier, label in entries if label}
    lookup.update({identifier.casefold(): identifier for identifier, _ in entries})
    if not lookup:
        raise _TelemetrError(status="error", error=f"Empty {kind} dictionary")
    _dictionary_cache[kind] = lookup
    return lookup


def _resolved_value(value: str, kind: str, lookup: dict[str, str]) -> str:
    identifier = lookup.get(value.casefold())
    if identifier is None and kind == "country":
        # The operator picks an ISO-3166 alpha-2 code, which matches neither an id
        # ("turkey") nor a name ("Turkey"); bridge it through the english name.
        english = _COUNTRY_NAME_BY_ALPHA2.get(value.upper())
        identifier = lookup.get(english.casefold()) if english is not None else None
    if identifier is None:
        raise _TelemetrError(
            status="unresolved_filter",
            error=f"Unknown {kind} filter: {value!r}",
        )
    return identifier


async def _resolve_filters(api_key: str, request: TelemetrSearchRequest) -> dict[str, str]:
    """Map the operator's filter values onto the ids the catalogue actually accepts."""
    resolved: dict[str, str] = {}
    for kind, value in (("country", request.country), ("language", request.language)):
        wanted = (value or "").strip()
        if not wanted:
            continue
        resolved[kind] = _resolved_value(wanted, kind, await _dictionary(api_key, kind))
    return resolved


async def _fetch_catalog(
    api_key: str,
    request: TelemetrSearchRequest,
    filters: dict[str, str],
) -> tuple[list[_CatalogRow], int | None]:
    """Run the catalogue search, retrying only what a second attempt could fix."""
    client = _get_client()
    attempts = settings.telemetr.max_retries + 1
    for attempt in range(attempts):
        last_attempt = attempt == attempts - 1
        try:
            response = await client.get(
                _url("/catalog/search"),
                headers={"x-api-key": api_key},
                params=_params(request, filters),
            )
        except _DETERMINISTIC_FAILURES as exc:
            raise _TelemetrError(status="error", error=_error_text(exc)) from exc
        except httpx.HTTPError as exc:
            if last_attempt:
                raise _TelemetrError(status="error", error=_error_text(exc)) from exc
        else:
            if response.status_code == _HTTP_OK:
                return _extract_items(_parse_body(response), request.limit)
            failure = _TelemetrError(
                status=_status_for(response.status_code),
                error=f"HTTP {response.status_code}: {_body_excerpt(response, api_key)}",
            )
            if last_attempt or not _retryable(response.status_code):
                raise failure
        await asyncio.sleep(settings.telemetr.retry_backoff_seconds)
    raise _TelemetrError(status="error", error="No attempt made")


async def _resolve_handles(api_key: str, rows: list[_CatalogRow]) -> list[TelemetrChannel]:
    """Attach the handle each catalogue row lacks, dropping rows that have none."""
    if not rows:
        return []
    ids = [row.internal_id for row in rows]
    handles: dict[str, str] = {}
    for start in range(0, len(ids), _INFO_BATCH_MAX_IDS):
        chunk = ids[start : start + _INFO_BATCH_MAX_IDS]
        body = await _get_json(api_key, "/channels/info-batch", {"ids": ",".join(chunk)})
        handles.update(_handles_from_batch(body))
    return [
        TelemetrChannel(
            username=handles[row.internal_id],
            title=row.title,
            members_count=row.members_count,
            country=row.country,
            language=row.language,
        )
        for row in rows
        if row.internal_id in handles
    ]


async def search_catalog(request: TelemetrSearchRequest) -> TelemetrSearchResult:
    """Search the Telemetr.io catalogue, classifying failures typed-ly.

    Never raises. A missing key maps to ``status="not_configured"`` without a request;
    a filter value no dictionary knows maps to ``status="unresolved_filter"`` carrying
    the offending value, because a silently empty result is what makes that class of
    bug invisible. Quota exhaustion (426), an inactive subscription (412), a rate limit
    (429) and the 4xx family each get their own status so the caller can tell "top up
    your plan" from "your key is wrong"; transport failures, 5xx and unreadable bodies
    stay ``status="error"``. Only a 5xx or a transport failure is retried, up to
    ``settings.telemetr.max_retries`` times with a short backoff.
    """
    api_key = request.api_key.strip()
    # Strip first: a whitespace-only key is not configured, and sending it would report
    # an auth error for a source the operator simply never set up.
    if not api_key:
        return TelemetrSearchResult(status="not_configured")

    try:
        filters = await _resolve_filters(api_key, request)
        rows, total = await _fetch_catalog(api_key, request, filters)
        items = await _resolve_handles(api_key, rows)
    except _TelemetrError as failure:
        return failure.result
    return TelemetrSearchResult(status="ok", items=items, total_count=total)
