"""The start dialog's geo consent and «Вернуть в прогрев» (a new wave from zero)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    create_account,
    fetch_warming_state,
    mark_nc_handed_off,
    mark_promoted_to_nc,
    save_warming_settings,
    update_proxy_check,
    upsert_warming_state,
)
from core.repositories.warming_marks import store_geo_ack
from schemas.accounts import AccountCreate
from schemas.proxy import ProxyCheckUpdate
from schemas.warming import StartWarmingRequest, WarmingStateWrite
from services import _account_owner, warming
from services.warming import _runtime
from tests.factories import seed_account_proxy
from tests.services.warming._support import _fake_loop

if TYPE_CHECKING:
    from schemas.device_fingerprint import DeviceType

# A Mexico City number behind a US proxy: the pair the dialog warns about.
_MX_PHONE = "+525512345678"


@pytest.fixture(autouse=True)
def _quiet_start(monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    _account_owner.reset_for_tests()
    yield
    for task in list(warming._RUNTIME.values()):
        task.cancel()
    _account_owner.reset_for_tests()


async def _account(
    proxy_country: str | None = "US",
    phone: str = _MX_PHONE,
    session_device: DeviceType = "desktop",
) -> None:
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(
        AccountCreate(account_id="acc-1", phone=phone, session_device=session_device),
    )
    if proxy_country is not None:
        proxy_id = await seed_account_proxy("acc-1", host="1.2.3.4")
        await update_proxy_check(
            ProxyCheckUpdate(
                proxy_id=proxy_id,
                status="tcp_working",
                exit_ip="9.9.9.9",
                country_code=proxy_country,
            ),
        )


@pytest.mark.asyncio
async def test_an_accepted_mismatch_is_stored_as_its_country_pair() -> None:
    await _account()

    await warming.start_warming_with_consent(
        StartWarmingRequest(account_id="acc-1", accept_geo_mismatch=True)
    )

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "active"
    assert record.geo_ack == "MX>US"


@pytest.mark.asyncio
async def test_a_mismatch_never_refuses_the_start_and_unchecked_stores_nothing() -> None:
    await _account()

    card = await warming.start_warming_with_consent(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert card.state == "active"
    assert record is not None
    assert record.geo_ack is None


@pytest.mark.asyncio
async def test_consent_without_a_mismatch_stores_nothing() -> None:
    await _account(proxy_country="MX")

    await warming.start_warming_with_consent(
        StartWarmingRequest(account_id="acc-1", accept_geo_mismatch=True)
    )

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.geo_ack is None


@pytest.mark.asyncio
async def test_consent_for_an_account_without_a_proxy_stores_nothing() -> None:
    await _account(proxy_country=None)

    await warming.start_warming_with_consent(
        StartWarmingRequest(account_id="acc-1", accept_geo_mismatch=True)
    )

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.geo_ack is None


@pytest.mark.asyncio
async def test_an_unknown_account_is_refused_before_any_consent() -> None:
    with pytest.raises(warming.UnknownAccountError):
        await warming.start_warming_with_consent(
            StartWarmingRequest(account_id="ghost", accept_geo_mismatch=True)
        )


@pytest.mark.asyncio
async def test_the_board_shows_the_consent_only_for_the_pair_it_was_given_for() -> None:
    await _account()
    await warming.start_warming_with_consent(
        StartWarmingRequest(account_id="acc-1", accept_geo_mismatch=True)
    )

    (card,) = (await warming.load_board()).warming
    assert (card.geo_status, card.geo_accepted) == ("mismatch", True)

    # A consent for another proxy country no longer covers the account.
    await store_geo_ack("acc-1", "MX>DE")
    (card,) = (await warming.load_board()).warming
    assert (card.geo_status, card.geo_accepted) == ("mismatch", False)


async def _graduate(*, warmed_days: int, handed_off: bool = False) -> str:
    started = (datetime.now(UTC) - timedelta(days=20)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="idle",
            started_at=started,
            stopped_at=datetime.now(UTC).isoformat(),
            target_days=14,
            warmed_days=warmed_days,
            last_warmed_date="2026-06-01",
        ),
    )
    await mark_promoted_to_nc("acc-1")
    if handed_off:
        await mark_nc_handed_off("acc-1")
    return started


@pytest.mark.asyncio
async def test_rewarm_starts_the_next_wave_from_zero_with_the_dialog_choice() -> None:
    await _account(proxy_country="MX")
    started = await _graduate(warmed_days=14, handed_off=False)

    card = await warming.rewarm_account(
        StartWarmingRequest(account_id="acc-1", target_days=21, activity_persona="calm")
    )

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert card.state == "active"
    assert (record.promoted_to_nc, record.nc_handed_off) == (False, False)
    # A fresh stint: a new clock, no worked days carried, the next wave.
    assert record.started_at != started
    assert record.warmed_days == 0
    assert (record.wave, card.wave) == (2, 2)
    assert (record.target_days, record.activity_persona) == (21, "calm")
    assert record.stopped_at is None


@pytest.mark.asyncio
async def test_a_plain_start_of_a_graduated_account_still_opens_a_new_stint() -> None:
    await _account(proxy_country="MX")
    started = await _graduate(warmed_days=14)

    await warming.start_warming_with_consent(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.started_at != started
    assert record.warmed_days == 0


@pytest.mark.parametrize("handed_off", [False, True])
@pytest.mark.asyncio
async def test_rewarm_refuses_an_account_not_on_the_warmed_card(*, handed_off: bool) -> None:
    await _account(proxy_country="MX")
    if handed_off:
        await _graduate(warmed_days=14, handed_off=True)
    else:
        await upsert_warming_state(
            WarmingStateWrite(
                account_id="acc-1",
                state="idle",
                started_at="2026-01-01T00:00:00+00:00",
                warmed_days=3,
            ),
        )

    with pytest.raises(warming.NotGraduatedError):
        await warming.rewarm_account(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "idle"
    assert "acc-1" not in warming._RUNTIME


@pytest.mark.asyncio
async def test_rewarm_records_a_geo_consent_too() -> None:
    await _account()
    await _graduate(warmed_days=5)

    await warming.rewarm_account(StartWarmingRequest(account_id="acc-1", accept_geo_mismatch=True))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.geo_ack == "MX>US"
    assert record.warmed_days == 0


@pytest.mark.asyncio
async def test_the_warmed_card_carries_the_geo_and_the_board_the_soft_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.soft_start, "days", 7)
    await _account()
    await _graduate(warmed_days=14)

    board = await warming.load_board()

    (warmed,) = board.warmed
    assert (warmed.geo_status, warmed.geo_accepted) == ("mismatch", False)
    assert board.soft_start.days == 7
    assert board.soft_start.last_day_comments == settings.soft_start.last_day_comments


@pytest.mark.asyncio
async def test_the_warmed_card_offers_the_accounts_own_devices_to_resume() -> None:
    await _account(session_device="mobile")
    await _graduate(warmed_days=14)

    board = await warming.load_board()

    (warmed,) = board.warmed
    assert warmed.devices == ["mobile"]
