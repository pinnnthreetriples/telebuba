"""Property-based tests for ``core.phone_geo.evaluate_geo``.

The verdict must be well-formed and never raise for any input, and must report
``unknown`` whenever the proxy country cannot be determined.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from core.phone_geo import evaluate_geo

_STATUSES = {"match", "mismatch", "unknown"}


@given(
    phone=st.one_of(st.none(), st.text()),
    proxy=st.one_of(st.none(), st.text()),
    lang=st.one_of(st.none(), st.text()),
)
def test_evaluate_geo_is_well_formed(
    phone: str | None, proxy: str | None, lang: str | None
) -> None:
    """For any input the verdict has a known status, uppercased proxy, no raise."""
    result = evaluate_geo(phone=phone, proxy_country=proxy, lang_code=lang)
    assert result.status in _STATUSES
    assert result.proxy_country is None or result.proxy_country == result.proxy_country.upper()


@given(phone=st.one_of(st.none(), st.text()))
def test_evaluate_geo_unknown_without_proxy(phone: str | None) -> None:
    """A missing/empty proxy country always yields ``unknown``, regardless of phone."""
    assert evaluate_geo(phone=phone, proxy_country=None).status == "unknown"
    assert evaluate_geo(phone=phone, proxy_country="").status == "unknown"


def test_evaluate_geo_matches_and_mismatches() -> None:
    """A resolvable phone matches its own country and mismatches another.

    Uses a US number as a concrete resolvable phone; proxy == phone-country is
    ``match`` (case-insensitive) and a different country is ``mismatch``.
    """
    match = evaluate_geo(phone="+14155552671", proxy_country="us")
    assert match.status == "match"
    assert match.phone_country == "US"
    mismatch = evaluate_geo(phone="+14155552671", proxy_country="de")
    assert mismatch.status == "mismatch"
