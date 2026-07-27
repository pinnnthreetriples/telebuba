from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass
from urllib.parse import quote

import httpx

# The raw HTTPS-over-SOCKS transport lives in ``_proxy_http`` (file-size budget).
# Re-exported so ``core.proxy_check.<name>`` keeps resolving for call sites and
# tests. ``_ProxyCheckError`` is defined there but belongs to both modules — it is
# the marker :func:`_short_error` below matches on.
from core._proxy_http import (  # noqa: F401 - re-export; only some names are used here.
    _decode_chunked,
    _fetch_https_through_proxy,
    _http_request,
    _parse_http_json,
    _ProxyCheckError,
    _read_limited,
)
from core.config import settings
from schemas.proxy import GeoStatus, ProxyCheckResult, ProxySettings

_MAX_ERROR_LENGTH = 240
_COUNTRY_CODE_LENGTH = 2

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _GeoRecord:
    provider: str
    country_code: str
    country_name: str | None
    asn: str | None = None


@dataclass(frozen=True, slots=True)
class _GeoOutcome:
    status: GeoStatus
    country_code: str | None = None
    country_name: str | None = None
    asn: str | None = None
    ipinfo_country_code: str | None = None
    maxmind_country_code: str | None = None
    error: str | None = None


async def check_proxy_connectivity(proxy: ProxySettings) -> ProxyCheckResult:
    try:
        exit_ip = await _fetch_exit_ip(proxy)
    except TimeoutError:
        return ProxyCheckResult(status="failed", last_error="Proxy check timed out")
    except OSError as exc:
        return _failed_result(exc)
    except Exception as exc:  # noqa: BLE001 - proxy libraries expose mixed exception types.
        return _failed_result(exc)

    # A reachable proxy must never be marked failed because geolocation errored.
    # The per-provider helpers already degrade to "unavailable", but guard the
    # whole lookup so any unexpected escape (e.g. a misconfigured base URL) can
    # never turn a working proxy into a failed one.
    try:
        geo = await _lookup_geolocation(exit_ip)
    except Exception:  # noqa: BLE001 - geo is best-effort; connectivity already succeeded.
        geo = _GeoOutcome(status="unavailable", error="Geolocation failed")
    return ProxyCheckResult(
        status="tcp_working",
        last_error=geo.error,
        exit_ip=exit_ip,
        country_code=geo.country_code,
        country_name=geo.country_name,
        geo_status=geo.status,
        ipinfo_country_code=geo.ipinfo_country_code,
        maxmind_country_code=geo.maxmind_country_code,
        asn=geo.asn,
        is_datacenter=_is_datacenter_asn(geo.asn),
    )


async def _fetch_exit_ip(proxy: ProxySettings) -> str:
    host = settings.proxy.exit_ip_host
    raw = await _fetch_https_through_proxy(
        proxy,
        host=host,
        port=settings.proxy.exit_ip_port,
        path=settings.proxy.exit_ip_path,
    )
    payload = _parse_http_json(raw)
    value = _optional_payload_str(payload.get("ip"))
    if value is None:
        msg = "Exit-IP endpoint returned no IP address"
        raise _ProxyCheckError(msg)
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        msg = "Exit-IP endpoint returned an invalid IP address"
        raise _ProxyCheckError(msg) from exc
    if not address.is_global:
        msg = "Proxy returned a non-public exit IP address"
        raise _ProxyCheckError(msg)
    return address.compressed


async def _lookup_geolocation(exit_ip: str) -> _GeoOutcome:
    providers: list[str] = []
    tasks: list[asyncio.Task[tuple[_GeoRecord | None, str | None]]] = []
    errors: list[str] = []

    if settings.proxy.ipinfo_token:
        providers.append("ipinfo")
        tasks.append(asyncio.create_task(_safe_ipinfo_lookup(exit_ip)))

    maxmind_configured = bool(
        settings.proxy.maxmind_account_id and settings.proxy.maxmind_license_key,
    )
    maxmind_partial = bool(
        settings.proxy.maxmind_account_id or settings.proxy.maxmind_license_key,
    )
    if maxmind_configured:
        providers.append("maxmind")
        tasks.append(asyncio.create_task(_safe_maxmind_lookup(exit_ip)))
    elif maxmind_partial:
        errors.append("MaxMind credentials are incomplete")

    if not tasks:
        return _GeoOutcome(
            status="unavailable",
            error=_join_errors(errors or ["Geolocation providers are not configured"]),
        )

    records: dict[str, _GeoRecord] = {}
    results = await asyncio.gather(*tasks)
    for provider, (record, error) in zip(providers, results, strict=True):
        if record is not None:
            records[provider] = record
        if error is not None:
            errors.append(error)
    return _merge_geo(records, errors)


async def _safe_ipinfo_lookup(exit_ip: str) -> tuple[_GeoRecord | None, str | None]:
    try:
        return await _lookup_ipinfo(exit_ip), None
    except (httpx.HTTPError, TypeError, ValueError):
        return None, "IPinfo lookup failed"


async def _safe_maxmind_lookup(exit_ip: str) -> tuple[_GeoRecord | None, str | None]:
    try:
        return await _lookup_maxmind(exit_ip), None
    except (httpx.HTTPError, TypeError, ValueError):
        return None, "MaxMind lookup failed"


async def _lookup_ipinfo(exit_ip: str) -> _GeoRecord:
    base_url = settings.proxy.ipinfo_base_url.rstrip("/")
    url = f"{base_url}/{quote(exit_ip, safe='')}"
    headers = {"Authorization": f"Bearer {settings.proxy.ipinfo_token}"}
    # trust_env=False: keep this lookup unconditionally direct — never route it
    # through an ambient HTTPS_PROXY env var (which would leak the exit IP).
    async with httpx.AsyncClient(
        timeout=settings.proxy.check_timeout_seconds,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        msg = "IPinfo returned non-object JSON"
        raise TypeError(msg)
    country_code = _country_code(payload.get("country_code"), "IPinfo")
    country_name = _optional_payload_str(payload.get("country"))
    asn_number = _optional_payload_str(payload.get("asn"))
    asn_name = _optional_payload_str(payload.get("as_name"))
    asn = " ".join(part for part in (asn_number, asn_name) if part) or None
    return _GeoRecord("ipinfo", country_code, country_name, asn)


async def _lookup_maxmind(exit_ip: str) -> _GeoRecord:
    base_url = settings.proxy.maxmind_base_url.rstrip("/")
    url = f"{base_url}/{quote(exit_ip, safe='')}"
    auth = httpx.BasicAuth(
        settings.proxy.maxmind_account_id,
        settings.proxy.maxmind_license_key,
    )
    async with httpx.AsyncClient(
        timeout=settings.proxy.check_timeout_seconds,
        follow_redirects=False,
        auth=auth,
        trust_env=False,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        msg = "MaxMind returned non-object JSON"
        raise TypeError(msg)
    country = payload.get("country")
    if not isinstance(country, dict):
        msg = "MaxMind returned no country"
        raise TypeError(msg)
    country_code = _country_code(country.get("iso_code"), "MaxMind")
    names = country.get("names")
    country_name = _optional_payload_str(names.get("en")) if isinstance(names, dict) else None
    return _GeoRecord("maxmind", country_code, country_name)


def _merge_geo(records: dict[str, _GeoRecord], errors: list[str]) -> _GeoOutcome:
    ipinfo = records.get("ipinfo")
    maxmind = records.get("maxmind")
    ipinfo_code = ipinfo.country_code if ipinfo else None
    maxmind_code = maxmind.country_code if maxmind else None

    if ipinfo and maxmind and ipinfo.country_code != maxmind.country_code:
        mismatch = (
            f"Geolocation mismatch: IPinfo={ipinfo.country_code}, MaxMind={maxmind.country_code}"
        )
        return _GeoOutcome(
            status="conflict",
            asn=ipinfo.asn,
            ipinfo_country_code=ipinfo_code,
            maxmind_country_code=maxmind_code,
            error=_join_errors([mismatch, *errors]),
        )

    selected = ipinfo or maxmind
    if selected is None:
        return _GeoOutcome(status="unavailable", error=_join_errors(errors))
    status: GeoStatus = "confirmed" if ipinfo and maxmind else "single_source"
    return _GeoOutcome(
        status=status,
        country_code=selected.country_code,
        country_name=selected.country_name,
        asn=ipinfo.asn if ipinfo else None,
        ipinfo_country_code=ipinfo_code,
        maxmind_country_code=maxmind_code,
        error=_join_errors(errors),
    )


def _country_code(value: object, provider: str) -> str:
    code = _optional_payload_str(value)
    if (
        code is None
        or len(code) != _COUNTRY_CODE_LENGTH
        or not code.isascii()
        or not code.isalpha()
    ):
        msg = f"{provider} returned an invalid country code"
        raise ValueError(msg)
    return code.upper()


def _join_errors(errors: list[str]) -> str | None:
    if not errors:
        return None
    return "; ".join(errors)[:_MAX_ERROR_LENGTH]


def _is_datacenter_asn(asn: str | None) -> bool:
    if not asn:
        return False
    lowered = asn.lower()
    return any(keyword in lowered for keyword in settings.proxy.datacenter_asn_keywords)


def _optional_payload_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _failed_result(exc: BaseException) -> ProxyCheckResult:
    """A bounded ``failed`` result, with the full text sent to the STDLIB logger.

    ``last_error`` reaches the ``proxies`` row, ``AccountRead.proxy_last_error`` and
    the operator's browser (the proxy-pool row title), so it must stay bounded and
    content-free (non-negotiable #12) — ``str(exc)`` is arbitrary third-party prose
    that names the proxy endpoint. Nothing pattern-matches the value: the UI renders
    it as a tooltip and every decision is driven by ``status``.

    Stdlib logging on purpose (mirrors ``_profile._mark_account_status``): a
    ``log_event`` code needs SPA copy in both locales for what is a diagnostic, and
    ``services.proxies.check_proxy`` already records the check itself under
    ``proxy_checked``. It is also the only destination here that is genuinely not
    operator-visible as prose — ``log_event`` persists ``extra`` to the ``logs``
    table and ``GET /logs`` serves it back as ``LogEntry.extra`` (``GET /events``
    streams it), so routing an unbounded ``str(exc)`` through ``extra`` would put
    it in an HTTP body just the same. Nothing configures stdlib logging here, so
    this lands on the process's stderr (the uvicorn console) via
    ``logging.lastResort`` — not in ``logs``, not in loguru's ``debug.log``, and on
    no route.
    """
    logger.warning(
        "proxy check failed (error_type=%s): %s",
        type(exc).__name__,
        exc,
    )
    return ProxyCheckResult(status="failed", last_error=_short_error(exc))


def _short_error(exc: BaseException) -> str:
    """This module's own diagnostics keep their prose; anything else is a class name."""
    if isinstance(exc, _ProxyCheckError):
        return (str(exc).strip() or type(exc).__name__)[:_MAX_ERROR_LENGTH]
    return type(exc).__name__
