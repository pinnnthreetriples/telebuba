"""The OTP session store: number normalization, one number per phone, expiry and pruning."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from services.telephony import _session as session_store
from services.telephony import (
    clear_sessions,
    find_active_session,
    get_session,
    normalize_phone,
    start_waiting_session,
    update_session_status,
)


@pytest.fixture(autouse=True)
def _reset() -> None:
    clear_sessions()


@pytest.mark.parametrize(
    ("raw_phone", "expected"),
    [
        ("+7 (999) 123-45-67", "+79991234567"),
        ("8-999-123-45-67", "+79991234567"),
        ("89991234567", "+79991234567"),
        ("79991234567", "+79991234567"),
        ("+1 555-000-1111", "+15550001111"),
        ("9991234567", "+79991234567"),
        ("0079991234567", "+79991234567"),
        ("00 44 20 7946 0958", "+442079460958"),
        ("+79991234567 ", "+79991234567"),
        ("   ", ""),
        ("abc", ""),
    ],
)
def test_normalize_phone_handles_gateway_formats(raw_phone: str, expected: str) -> None:
    assert normalize_phone(raw_phone) == expected


def test_a_national_number_takes_the_configured_country_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.telephony, "default_country_code", "1")

    assert normalize_phone("5550001111") == "+15550001111"


def test_a_number_without_digits_cannot_be_waited_on() -> None:
    with pytest.raises(ValueError, match="invalid phone_number"):
        start_waiting_session("telebuba-acc1", "no digits")


def test_re_registering_a_phone_forgets_its_old_number() -> None:
    start_waiting_session("telebuba-acc1", "+79990000001")
    start_waiting_session("telebuba-acc1", "+79990000002")

    assert find_active_session("+79990000001") is None
    active = find_active_session("+79990000002")
    assert active is not None
    assert active.phone_id == "telebuba-acc1"


def test_a_number_moves_to_the_phone_that_registers_it_last() -> None:
    start_waiting_session("telebuba-acc1", "+79990000001")
    start_waiting_session("telebuba-acc2", "+79990000001")

    active = find_active_session("+79990000001")
    assert active is not None
    assert active.phone_id == "telebuba-acc2"
    assert get_session("telebuba-acc1") is None


def test_a_session_that_got_its_code_is_no_longer_active() -> None:
    start_waiting_session("telebuba-acc1", "+79990000001")
    update_session_status("telebuba-acc1", "otp_received", last_code="12345")

    assert find_active_session("+79990000001") is None


def test_an_expired_wait_reads_as_timeout() -> None:
    session = start_waiting_session("telebuba-acc1", "+79990000001", ttl_seconds=60)
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    assert find_active_session("+79990000001") is None
    expired = get_session("telebuba-acc1")
    assert expired is not None
    assert expired.status == "timeout"


def test_the_wait_lasts_the_configured_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.telephony, "session_ttl_seconds", 42)

    session = start_waiting_session("telebuba-acc1", "+79990000001")

    assert (session.expires_at - session.created_at).total_seconds() == 42


def test_ended_sessions_are_pruned_after_the_retention_but_busy_ones_stay() -> None:
    old = datetime.now(UTC) - timedelta(seconds=settings.telephony.session_retention_seconds + 1)
    ended = start_waiting_session("telebuba-old", "+79990000001")
    update_session_status("telebuba-old", "completed")
    ended.updated_at = old
    busy = start_waiting_session("telebuba-busy", "+79990000002")
    update_session_status("telebuba-busy", "typing")
    busy.updated_at = old

    start_waiting_session("telebuba-new", "+79990000003")

    assert get_session("telebuba-old") is None
    assert "+79990000001" not in session_store._PHONE_TO_ID
    assert get_session("telebuba-busy") is busy


def test_updating_a_phone_without_a_session_is_a_no_op() -> None:
    assert update_session_status("telebuba-none", "failed", error_code="emulator_busy") is None


def test_a_status_update_records_and_clears_the_error_code() -> None:
    start_waiting_session("telebuba-acc1", "+79990000001")

    failed = update_session_status("telebuba-acc1", "failed", error_code="emulator_busy")
    assert failed is not None
    assert failed.error_code == "emulator_busy"

    retried = update_session_status("telebuba-acc1", "typing", last_sms_text="Telegram code: 12345")
    assert retried is not None
    assert retried.error_code is None
    assert retried.last_sms_text == "Telegram code: 12345"
