"""The SMS dispatcher: matching, reading pause, typing guards, failures and races."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import Instance
from core.emulator._errors import EmulatorError
from schemas.telephony import InboundSmsWebhook
from services.accounts import DeviceRefusedError, EmulatorBusyError, EmulatorRefusedError
from services.telephony import (
    INBOX_MAX_MESSAGES,
    StaleWebhookError,
    _dispatcher,
    get_phone_status,
    handle_inbound_sms,
    list_inbox,
    manual_input_otp,
    start_phone_wait,
    start_waiting_session,
    wait_pending_dispatches,
)

if TYPE_CHECKING:
    from tests.services.telephony.conftest import FakePhone

_PHONE = "telebuba-acc1"
_NUMBER = "+79991234567"


def _sms(text: str = "Telegram code: 48291", **overrides: object) -> InboundSmsWebhook:
    fields: dict[str, object] = {"phone": _NUMBER, "sender": "Telegram", "text": text}
    fields.update(overrides)
    return InboundSmsWebhook.model_validate(fields)


@pytest.mark.asyncio
async def test_a_matched_code_is_typed_after_the_reading_pause(phone: FakePhone) -> None:
    start_waiting_session(_PHONE, "8 (999) 123-45-67")

    response = await handle_inbound_sms(_sms())
    pending = await get_phone_status(_PHONE)
    await wait_pending_dispatches()

    assert (response.matched, response.code, response.duplicate) == (True, "48291", False)
    assert pending.status == "otp_received"
    telephony = settings.telephony
    assert phone.pauses == [
        (telephony.reading_pause_min_seconds, telephony.reading_pause_max_seconds),
    ]
    assert phone.typed == [(f"serial-{_PHONE}", "48291")]
    final = await get_phone_status(_PHONE)
    assert (final.status, final.last_code, final.error_code) == ("completed", "48291", None)


@pytest.mark.asyncio
async def test_the_pause_is_a_real_non_blocking_sleep(
    phone: FakePhone,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_dispatcher._RNG, "uniform", lambda _low, _high: 0.05)
    start_waiting_session(_PHONE, _NUMBER)

    await handle_inbound_sms(_sms())
    ticks = 0
    while not phone.typed:
        ticks += 1
        await asyncio.sleep(0.005)

    assert ticks > 1  # the loop kept running while the dispatch slept


@pytest.mark.asyncio
async def test_a_phone_off_its_code_screen_is_never_typed_into(phone: FakePhone) -> None:
    phone.screen = "chats"
    start_waiting_session(_PHONE, _NUMBER)

    await handle_inbound_sms(_sms())
    await wait_pending_dispatches()

    assert phone.typed == []
    status = await get_phone_status(_PHONE)
    assert (status.status, status.error_code) == ("failed", "emulator_field_missing")
    assert phone.events == [
        ("telephony_otp_typing_failed", {"phone_id": _PHONE, "code": "emulator_field_missing"}),
    ]


@pytest.mark.asyncio
async def test_a_container_lost_mid_typing_ends_the_wait_as_failed(phone: FakePhone) -> None:
    phone.fail_with = EmulatorError("emulator_command_failed")
    start_waiting_session(_PHONE, _NUMBER)

    await handle_inbound_sms(_sms())
    await wait_pending_dispatches()  # must not raise the task's error

    status = await get_phone_status(_PHONE)
    assert (status.status, status.error_code) == ("failed", "emulator_command_failed")


@pytest.mark.asyncio
async def test_an_unexpected_typing_error_is_contained(phone: FakePhone) -> None:
    phone.fail_with = RuntimeError("boom")
    start_waiting_session(_PHONE, _NUMBER)

    await handle_inbound_sms(_sms())
    await wait_pending_dispatches()

    status = await get_phone_status(_PHONE)
    assert (status.status, status.error_code) == ("failed", "emulator_command_failed")


@pytest.mark.asyncio
async def test_a_phone_held_by_warming_is_not_typed_into(phone: FakePhone) -> None:
    phone.activity = "warming"
    start_waiting_session(_PHONE, _NUMBER)

    await handle_inbound_sms(_sms())
    await wait_pending_dispatches()

    assert phone.typed == []
    assert (await get_phone_status(_PHONE)).error_code == "emulator_busy"


@pytest.mark.asyncio
async def test_a_second_sms_during_the_pause_is_kept_but_not_typed(phone: FakePhone) -> None:
    start_waiting_session(_PHONE, _NUMBER)

    first = await handle_inbound_sms(_sms("Telegram code: 11111"))
    second = await handle_inbound_sms(_sms("Telegram code: 22222"))
    await wait_pending_dispatches()

    assert (first.matched, second.matched) == (True, False)
    assert phone.typed == [(f"serial-{_PHONE}", "11111")]
    inbox = await list_inbox(_PHONE)
    assert [m.code for m in inbox.messages] == ["22222", "11111"]


@pytest.mark.asyncio
async def test_a_new_wait_during_the_pause_drops_the_old_code(phone: FakePhone) -> None:
    phone.gate = asyncio.Event()
    start_waiting_session(_PHONE, _NUMBER)
    await handle_inbound_sms(_sms())

    start_waiting_session(_PHONE, _NUMBER)
    phone.gate.set()
    await wait_pending_dispatches()

    assert phone.typed == []
    assert (await get_phone_status(_PHONE)).status == "waiting_otp"


@pytest.mark.asyncio
async def test_manual_input_supersedes_a_code_still_in_its_pause(
    phone: FakePhone,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_dispatcher._RNG, "uniform", lambda _low, _high: 10.0)
    start_waiting_session(_PHONE, _NUMBER)
    await handle_inbound_sms(_sms("Telegram code: 11111"))

    result = await manual_input_otp(_PHONE, "99999")
    await wait_pending_dispatches()

    assert result.line_status == "completed"
    assert phone.typed == [(f"serial-{_PHONE}", "99999")]
    assert (await get_phone_status(_PHONE)).status == "completed"


@pytest.mark.asyncio
async def test_manual_input_waits_for_a_code_being_typed(phone: FakePhone) -> None:
    phone.gate = asyncio.Event()
    start_waiting_session(_PHONE, _NUMBER)
    await handle_inbound_sms(_sms("Telegram code: 11111"))
    await asyncio.sleep(0)  # the dispatch takes the lock and starts typing

    manual = asyncio.create_task(manual_input_otp(_PHONE, "99999"))
    await asyncio.sleep(0)
    assert phone.typed == []
    phone.gate.set()
    await manual
    await wait_pending_dispatches()

    assert [code for _serial, code in phone.typed] == ["11111", "99999"]


@pytest.mark.asyncio
async def test_a_refused_manual_input_raises_its_code_and_marks_the_line(
    phone: FakePhone,
) -> None:
    phone.screen = "password"
    start_waiting_session(_PHONE, _NUMBER)

    with pytest.raises(EmulatorRefusedError) as refused:
        await manual_input_otp(_PHONE, "12345")

    assert refused.value.code == "emulator_field_missing"
    assert phone.typed == []
    assert (await get_phone_status(_PHONE)).status == "failed"


@pytest.mark.asyncio
async def test_a_manual_input_on_a_busy_phone_is_refused(phone: FakePhone) -> None:
    phone.activity = "starting"

    with pytest.raises(EmulatorBusyError):
        await manual_input_otp(_PHONE, "12345")


@pytest.mark.asyncio
async def test_a_manual_input_without_a_session_still_types(phone: FakePhone) -> None:
    result = await manual_input_otp(_PHONE, "12345")

    assert result.code == "12345"
    assert phone.typed == [(f"serial-{_PHONE}", "12345")]
    assert (await get_phone_status(_PHONE)).status == "idle"


@pytest.mark.asyncio
async def test_start_phone_wait_waits_on_the_accounts_number(phone: FakePhone) -> None:
    phone.account_phone = "8 999 123 45 67"

    status = await start_phone_wait(_PHONE)

    assert (status.status, status.phone_number) == ("waiting_otp", _NUMBER)
    assert status.remaining_seconds > 0


@pytest.mark.asyncio
async def test_start_phone_wait_refuses_an_account_without_a_number(phone: FakePhone) -> None:
    phone.account_phone = None

    with pytest.raises(DeviceRefusedError) as refused:
        await start_phone_wait(_PHONE)

    assert refused.value.code == "device_phone_unknown"


@pytest.mark.asyncio
async def test_start_phone_wait_refuses_a_phone_being_started(phone: FakePhone) -> None:
    phone.activity = "starting"

    with pytest.raises(EmulatorBusyError):
        await start_phone_wait(_PHONE)


@pytest.mark.asyncio
@pytest.mark.usefixtures("phone")
async def test_start_phone_wait_refuses_an_id_that_is_not_a_phone() -> None:
    with pytest.raises(EmulatorRefusedError) as refused:
        await start_phone_wait("not-a-phone")

    assert refused.value.code == "emulator_not_found"


@pytest.mark.asyncio
async def test_an_unmatched_sms_lands_in_the_inbox_without_typing(phone: FakePhone) -> None:
    response = await handle_inbound_sms(
        _sms("<#> Ваш код WhatsApp: 321-654", phone="+79990001122", sender="WhatsApp"),
    )

    assert (response.matched, response.code) == (False, "321654")
    assert phone.typed == []
    (message,) = (await list_inbox()).messages
    assert (message.phone, message.platform, message.phone_id) == (
        "+79990001122",
        "whatsapp",
        None,
    )


@pytest.mark.asyncio
async def test_a_phones_inbox_never_shows_other_numbers(phone: FakePhone) -> None:
    await handle_inbound_sms(_sms("code 4821", phone="+15550001111", sender="Bank"))
    start_waiting_session(_PHONE, _NUMBER)
    phone.screen = "chats"  # keep the matched one from being typed
    await handle_inbound_sms(_sms())
    await wait_pending_dispatches()

    assert (await list_inbox("telebuba-unrelated")).messages == []
    mine = await list_inbox(_PHONE)
    assert [m.phone for m in mine.messages] == [_NUMBER]


@pytest.mark.asyncio
async def test_the_inbox_keeps_the_newest_fifty() -> None:
    for i in range(65):
        await handle_inbound_sms(
            _sms(f"Telegram code: {10000 + i}", phone=f"+7999000{i:04d}"),
        )

    inbox = await list_inbox()
    assert len(inbox.messages) == INBOX_MAX_MESSAGES
    assert (inbox.messages[0].code, inbox.messages[-1].code) == ("10064", "10015")


@pytest.mark.asyncio
async def test_the_same_sms_is_answered_once_within_the_window() -> None:
    first = await handle_inbound_sms(_sms())
    again = await handle_inbound_sms(_sms(phone="8 999 123-45-67"))
    other_sender = await handle_inbound_sms(_sms(sender="Info"))

    assert (first.duplicate, again.duplicate, other_sender.duplicate) == (False, True, False)
    assert again.code is None


def test_the_replay_window_forgets_old_entries() -> None:
    ttl = settings.telephony.dedup_ttl_seconds

    assert _dispatcher._is_duplicate_and_record("a", now_mono=0.0) is False
    assert _dispatcher._is_duplicate_and_record("a", now_mono=ttl - 1) is True
    assert _dispatcher._is_duplicate_and_record("b", now_mono=ttl - 1) is False
    assert _dispatcher._is_duplicate_and_record("a", now_mono=ttl) is False
    assert list(_dispatcher._SEEN_WEBHOOKS) == ["b", "a"]


@pytest.mark.asyncio
@pytest.mark.parametrize("offset_seconds", [-3600, 3600])
async def test_a_timestamp_outside_the_skew_is_refused(offset_seconds: int) -> None:
    stale = datetime.now(UTC) + timedelta(seconds=offset_seconds)

    with pytest.raises(StaleWebhookError):
        await handle_inbound_sms(_sms(timestamp=stale.isoformat()))

    assert (await list_inbox()).messages == []


@pytest.mark.asyncio
async def test_a_recent_gateway_timestamp_is_kept_on_the_message() -> None:
    received = datetime.now(UTC) - timedelta(seconds=30)

    await handle_inbound_sms(_sms(timestamp=received.isoformat()))

    (message,) = (await list_inbox()).messages
    assert message.timestamp == received


@pytest.mark.asyncio
async def test_a_flash_call_types_the_last_digits_of_the_caller(phone: FakePhone) -> None:
    start_waiting_session(_PHONE, _NUMBER)

    response = await handle_inbound_sms(
        _sms("", kind="flash_call", sender="call", caller_id="+7 (495) 123-48-29"),
    )
    await wait_pending_dispatches()

    assert (response.matched, response.code) == (True, "4829")
    assert phone.typed == [(f"serial-{_PHONE}", "4829")]


@pytest.mark.asyncio
async def test_an_empty_sms_from_a_number_is_not_a_flash_call(phone: FakePhone) -> None:
    start_waiting_session(_PHONE, _NUMBER)

    response = await handle_inbound_sms(_sms("", sender="+79161234567"))

    assert (response.matched, response.code) == (False, None)
    assert phone.typed == []


@pytest.mark.asyncio
async def test_no_serial_while_the_emulator_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.emulator, "enabled", False)

    with pytest.raises(EmulatorError) as refused:
        await _dispatcher._resolve_serial(_PHONE)

    assert refused.value.code == "mobile_emulator_disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("instance", "expected"),
    [
        (None, "emulator_not_running"),
        (Instance(index=2, name=_PHONE, running=False), "emulator_not_running"),
        (Instance(index=2, name=_PHONE, running=True, paused=True), "emulator_not_running"),
        (OSError("docker gone"), "emulator_command_failed"),
    ],
)
async def test_no_serial_for_a_pod_that_is_not_up(
    monkeypatch: pytest.MonkeyPatch,
    instance: Instance | OSError | None,
    expected: str,
) -> None:
    async def find_instance(_name: str) -> Instance | None:
        if isinstance(instance, OSError):
            raise instance
        return instance

    monkeypatch.setattr(settings.emulator, "enabled", True)
    monkeypatch.setattr(_dispatcher.redroid, "find_instance", find_instance)

    with pytest.raises(EmulatorError) as refused:
        await _dispatcher._resolve_serial(_PHONE)

    assert refused.value.code == expected


@pytest.mark.asyncio
async def test_a_running_pod_answers_with_its_adb_serial(monkeypatch: pytest.MonkeyPatch) -> None:
    async def find_instance(_name: str) -> Instance:
        return Instance(index=3, name=_PHONE, running=True)

    monkeypatch.setattr(settings.emulator, "enabled", True)
    monkeypatch.setattr(_dispatcher.redroid, "find_instance", find_instance)

    serial = await _dispatcher._resolve_serial(_PHONE)

    assert serial == _dispatcher.redroid.adb_serial(3)
