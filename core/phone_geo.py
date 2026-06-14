"""Phone-number geo helpers — the only place ``phonenumbers`` is imported.

Resolves a phone number to its ISO-3166 country and a representative IANA
timezone, and compares that against the proxy's exit country to flag a geo
mismatch. Per non-negotiable #6 the third-party library is wrapped here in
``core/``; services and features consume the typed :class:`GeoMatch`.
"""

from __future__ import annotations

import phonenumbers
from phonenumbers import timezone as pn_timezone

from schemas.geo import GeoMatch

_UNKNOWN_TZ = "Etc/Unknown"


def _parse(phone: str | None) -> phonenumbers.PhoneNumber | None:
    if not phone:
        return None
    candidate = phone if phone.startswith("+") else f"+{phone}"
    try:
        return phonenumbers.parse(candidate, None)
    except phonenumbers.NumberParseException:
        return None


def country_for_phone(phone: str | None) -> str | None:
    """ISO-3166 alpha-2 country for an E.164 phone number, or ``None``."""
    parsed = _parse(phone)
    if parsed is None:
        return None
    return phonenumbers.region_code_for_number(parsed) or None


def timezone_for_phone(phone: str | None) -> str | None:
    """A representative IANA timezone for a phone number, or ``None``.

    Numbers on a shared calling code (e.g. +7 → RU/KZ) map to many zones; we
    take the first as a representative for local-time scheduling.
    """
    parsed = _parse(phone)
    if parsed is None:
        return None
    zones = pn_timezone.time_zones_for_number(parsed)
    zone = zones[0] if zones else None
    return zone if zone and zone != _UNKNOWN_TZ else None


def _lang_region(lang_code: str | None) -> str | None:
    """The region part of a ``ll-CC`` language tag (``ru-RU`` → ``RU``)."""
    if not lang_code or "-" not in lang_code:
        return None
    return lang_code.split("-", 1)[1].upper() or None


def evaluate_geo(
    *,
    phone: str | None,
    proxy_country: str | None,
    lang_code: str | None = None,
) -> GeoMatch:
    """Compare phone country vs proxy country (and language) into a verdict.

    Never blocks — a mismatch is a warning + risk signal (product decision).
    """
    phone_country = country_for_phone(phone)
    proxy = (proxy_country or "").upper() or None
    lang_country = _lang_region(lang_code)
    lang_matches = (
        None if lang_country is None or phone_country is None else lang_country == phone_country
    )

    if phone_country is None or proxy is None:
        return GeoMatch(
            status="unknown",
            phone_country=phone_country,
            proxy_country=proxy,
            lang_country=lang_country,
            lang_matches=lang_matches,
            message="Could not determine phone or proxy country",
        )
    if phone_country == proxy:
        return GeoMatch(
            status="match",
            phone_country=phone_country,
            proxy_country=proxy,
            lang_country=lang_country,
            lang_matches=lang_matches,
        )
    return GeoMatch(
        status="mismatch",
        phone_country=phone_country,
        proxy_country=proxy,
        lang_country=lang_country,
        lang_matches=lang_matches,
        message=f"Proxy country {proxy} does not match phone country {phone_country}",
    )
