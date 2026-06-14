"""Tests for ``core.phone_geo`` — phone→country/timezone and geo matching."""

from __future__ import annotations

from core.phone_geo import country_for_phone, evaluate_geo, timezone_for_phone


def test_country_for_phone_resolves_e164() -> None:
    assert country_for_phone("+12025550123") == "US"
    assert country_for_phone("77011234567") == "KZ"  # tolerates a missing leading +


def test_country_for_phone_handles_bad_input() -> None:
    assert country_for_phone(None) is None
    assert country_for_phone("garbage") is None


def test_timezone_for_phone() -> None:
    assert timezone_for_phone("+12025550123") == "America/New_York"
    assert timezone_for_phone(None) is None


def test_evaluate_geo_match() -> None:
    verdict = evaluate_geo(phone="+12025550123", proxy_country="us", lang_code="en-US")
    assert verdict.status == "match"
    assert verdict.phone_country == "US"
    assert verdict.proxy_country == "US"
    assert verdict.lang_matches is True


def test_evaluate_geo_mismatch() -> None:
    verdict = evaluate_geo(phone="+77011234567", proxy_country="US", lang_code="ru-RU")
    assert verdict.status == "mismatch"
    assert verdict.phone_country == "KZ"
    assert verdict.proxy_country == "US"
    assert verdict.lang_matches is False
    assert verdict.message is not None
    assert "KZ" in verdict.message


def test_evaluate_geo_unknown_when_country_missing() -> None:
    verdict = evaluate_geo(phone=None, proxy_country="US")
    assert verdict.status == "unknown"
    assert verdict.phone_country is None
    assert verdict.lang_matches is None
