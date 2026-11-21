"""D13 through the gateway: a routine probe waits out the account's local night, nothing else does.

The account's zone is its phone number's (``core.phone_geo``); the tests name the zone through that
seam. ``Etc/GMT-3`` is UTC+3 all year, so the clock readings below hold in every season.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from schemas.spam_status import SpamBotHistory
from services import spam_intel
from services.spam_intel import _gateway, _history
from services.spam_intel._night import night_until

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.spam_intel import Purpose
    from tests.services.spam_intel.conftest import Clock, FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]

_FIXED = "Etc/GMT-3"  # UTC+3 all year
_NIGHT = datetime(2026, 11, 18, 23, 30, tzinfo=UTC)  # 02:30 local
_DAY = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)  # 12:00 local


@pytest.fixture(autouse=True)
def _long_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start the process well before the fixed clock readings, so the startup quiet never applies.

    ``_STARTED`` is the real import time; once the wall clock passes ``_NIGHT``/``_DAY`` the
    process age would go negative and every routine ask would defer as ``startup_quiet``.
    """
    monkeypatch.setattr(_gateway, "_STARTED", datetime(2026, 11, 1, tzinfo=UTC))


@pytest.fixture
def zone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_gateway, "timezone_for_phone", lambda _phone: _FIXED)


@pytest.mark.asyncio
@pytest.mark.usefixtures("zone")
async def test_a_routine_ask_at_local_night_is_deferred_to_the_morning(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("acc-1")
    clock.at = _NIGHT
    verdict = await spam_intel.ask("acc-1", purpose="routine")
    assert (verdict.status, verdict.served_from, verdict.reason) == (
        "unknown",
        "deferred",
        "local_night",
    )
    expected = night_until(_NIGHT, _FIXED, "acc-1", settings.spam_intel)
    assert expected is not None
    assert spam_intel.parse_retry(verdict) == expected
    assert probe.calls == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("zone")
async def test_the_same_routine_ask_by_day_probes(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("acc-1")
    clock.at = _DAY
    assert (await spam_intel.ask("acc-1", purpose="routine")).served_from == "probe"
    assert probe.calls == ["acc-1"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("zone")
@pytest.mark.parametrize("purpose", ["decision", "measurement"])
async def test_a_decision_or_a_measurement_is_never_delayed_by_the_night(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock, purpose: Purpose
) -> None:
    await make_account("acc-1")
    clock.at = _NIGHT
    verdict = await spam_intel.ask("acc-1", force=True, purpose=purpose)
    assert verdict.served_from == "probe"
    assert probe.calls == ["acc-1"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("zone")
async def test_the_operators_click_is_never_delayed_by_the_night(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("acc-1")
    clock.at = _NIGHT
    verdict = await spam_intel.operator_check("acc-1", confirm_out_of_turn=True)
    assert verdict.served_from == "probe"
    assert probe.calls == ["acc-1"]


@pytest.mark.asyncio
async def test_an_account_whose_zone_is_unknown_is_never_held(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_gateway, "timezone_for_phone", lambda _phone: None)
    await make_account("acc-1")
    clock.at = _NIGHT
    assert (await spam_intel.ask("acc-1", purpose="routine")).served_from == "probe"
    assert probe.calls == ["acc-1"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("zone")
async def test_with_the_switch_off_a_routine_ask_probes_at_night(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.spam_intel, "local_night_defer", False)
    await make_account("acc-1")
    clock.at = _NIGHT
    assert (await spam_intel.ask("acc-1", purpose="routine")).served_from == "probe"
    assert probe.calls == ["acc-1"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("zone")
async def test_a_night_deferral_asks_nothing_and_leaves_no_ledger_row(
    make_account: MakeAccount, clock: Clock
) -> None:
    from core.db import _get_engine  # noqa: PLC0415

    await make_account("acc-1")
    clock.at = _NIGHT
    await spam_intel.ask("acc-1", purpose="routine")
    with _get_engine().connect() as connection:
        rows = connection.exec_driver_sql("SELECT COUNT(*) FROM spam_probe_attempts").scalar()
    assert rows == 0


class _Reader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, account_id: str, _limit: int) -> SpamBotHistory:
        self.calls.append(account_id)
        return SpamBotHistory(account_id=account_id, texts=[])


@pytest.mark.asyncio
async def test_the_history_sweep_skips_an_account_in_a_transient_status_and_reads_it_later(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.db import _get_engine  # noqa: PLC0415

    reader = _Reader()
    monkeypatch.setattr(_history, "read_spambot_history", reader)
    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(_history, "_sleep", _sleep)
    await make_account("flooded", status="flood_wait")
    await make_account("fine")
    assert await _history.run_history_sweep() == 1
    assert reader.calls == ["fine"]
    assert max(slept, default=0.0) < 60.0  # it did not hold the sweep waiting for the status
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE accounts SET status = 'alive' WHERE account_id = 'flooded'"
        )
    assert await _history.run_history_sweep() == 1
    assert reader.calls == ["fine", "flooded"]
