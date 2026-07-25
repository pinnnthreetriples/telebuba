"""Telemetr.io channel-catalogue gateway — the external half of channel discovery.

The only module that talks HTTP to Telemetr.io. Mirrors ``core.gemini`` /
``core.openai``: the service passes a typed :class:`TelemetrSearchRequest` and
gets a :class:`TelemetrSearchResult` back — never an exception, so one flaky
external source can never abort a discovery run.

Endpoint: ``GET {base_url}/catalog/search`` with an ``x-api-key`` header.
``search_in_about`` is pinned on: matching the description as well as the title
roughly doubles recall for a topical keyword, which is exactly what discovery
wants. An empty key short-circuits to ``status="not_configured"`` before any
socket is opened — a skipped source, not a failure.
"""

from __future__ import annotations

import asyncio
from typing import cast

import httpx

from core.config import settings
from schemas.telemetr import TelemetrChannel, TelemetrSearchRequest, TelemetrSearchResult

_HTTP_OK = 200
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR_MIN = 500


class _ClientHolder:
    client: httpx.AsyncClient | None = None


_holder = _ClientHolder()


def _get_client() -> httpx.AsyncClient:
    """Return the shared AsyncClient, creating it on first use (reused across calls)."""
    if _holder.client is None:
        _holder.client = httpx.AsyncClient(timeout=settings.telemetr.timeout_seconds)
    return _holder.client


async def close_telemetr_client() -> None:
    """Close the shared AsyncClient. Called from the app lifespan on shutdown."""
    if _holder.client is not None:
        await _holder.client.aclose()
        _holder.client = None


def _endpoint() -> str:
    return f"{settings.telemetr.base_url}/catalog/search"


def _params(request: TelemetrSearchRequest) -> dict[str, str | int]:
    params: dict[str, str | int] = {
        "term": request.term,
        # Widen recall: a topical keyword usually appears in the description too.
        "search_in_about": "true",
        "limit": request.limit,
    }
    optional: dict[str, str | int | None] = {
        "country": request.country,
        "language": request.language,
        "members_min": request.members_min,
        "members_max": request.members_max,
    }
    params.update({key: value for key, value in optional.items() if value is not None})
    return params


def _parse_channel(entry: object) -> TelemetrChannel | None:
    if not isinstance(entry, dict):
        return None
    row = cast("dict[str, object]", entry)
    # Telemetr calls the handle "peer"; a row without one cannot be linked to a
    # campaign (campaign channels are handles), so it is dropped.
    peer = row.get("peer")
    if not isinstance(peer, str) or not peer.strip():
        return None
    title = row.get("title")
    members = row.get("members_count")
    return TelemetrChannel(
        username=peer.strip().lstrip("@"),
        title=title.strip() if isinstance(title, str) else "",
        members_count=int(members) if isinstance(members, int) else None,
    )


def _extract_items(body: object) -> list[TelemetrChannel]:
    # Documented shape is {"items": [...]}; tolerate a bare array too rather than
    # failing the whole source on an envelope change.
    rows: object = body
    if isinstance(body, dict):
        rows = cast("dict[str, object]", body).get("items")
    if not isinstance(rows, list):
        return []
    parsed = (_parse_channel(entry) for entry in rows)
    return [channel for channel in parsed if channel is not None]


def _is_transient(status_code: int) -> bool:
    return status_code == _HTTP_TOO_MANY_REQUESTS or status_code >= _HTTP_SERVER_ERROR_MIN


def _classify_response(response: httpx.Response) -> TelemetrSearchResult:
    if response.status_code == _HTTP_TOO_MANY_REQUESTS:
        # The free tier is 1000 requests/month, so this is a realistic outcome; the
        # service keeps the run alive on native results and surfaces the reason.
        return TelemetrSearchResult(
            status="rate_limited",
            error=f"HTTP 429: {response.text[:200]}",
        )
    if response.status_code != _HTTP_OK:
        return TelemetrSearchResult(
            status="error",
            error=f"HTTP {response.status_code}: {response.text[:200]}",
        )
    try:
        body = response.json()
    except ValueError as exc:
        return TelemetrSearchResult(status="error", error=f"Invalid JSON: {exc}")
    return TelemetrSearchResult(status="ok", items=_extract_items(body))


async def search_catalog(request: TelemetrSearchRequest) -> TelemetrSearchResult:
    """Search the Telemetr.io catalogue, classifying failures typed-ly.

    Never raises: HTTP errors, timeouts, and unexpected payloads map to
    ``status="error"``; a 429 maps to ``status="rate_limited"``; a missing key maps
    to ``status="not_configured"`` without a request. Retries a transient failure
    up to ``settings.telemetr.max_retries`` times with a short backoff.
    """
    if not request.api_key:
        return TelemetrSearchResult(status="not_configured")

    client = _get_client()
    attempts = settings.telemetr.max_retries + 1
    result = TelemetrSearchResult(status="error", error="No attempt made")
    for attempt in range(attempts):
        try:
            response = await client.get(
                _endpoint(),
                headers={"x-api-key": request.api_key},
                params=_params(request),
            )
        except httpx.HTTPError as exc:
            result = TelemetrSearchResult(status="error", error=f"{type(exc).__name__}: {exc}")
            transient = True
        else:
            result = _classify_response(response)
            transient = _is_transient(response.status_code)
        if not transient or attempt == attempts - 1:
            return result
        await asyncio.sleep(settings.telemetr.retry_backoff_seconds)
    return result
