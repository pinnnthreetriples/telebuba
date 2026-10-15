"""The self-search inside a real visit: the fence, the join cap, the interests it keeps."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from core.config import settings
from core.db import create_account, update_account_from_session_check
from schemas.accounts import AccountCreate
from schemas.telegram_actions import GetUserProfile
from schemas.telegram_session import TelegramSessionCheckResult
from schemas.warming import AddChannelsRequest, WarmingChannel, WarmingCycleRequest
from services import warming
from services.warming import _cycle, _seams
from services.warming._fleet import _account_channel_affinity
from tests.services.warming._pool_support import MX_PHONE, FakeTelegram
from tests.services.warming._support import _Recorder, _set_settings

if TYPE_CHECKING:
    from schemas.warming import WarmingIntensity


@pytest_asyncio.fixture
async def visit(monkeypatch: pytest.MonkeyPatch) -> tuple[_Recorder, FakeTelegram]:
    await create_account(AccountCreate(account_id="acc-1", phone=MX_PHONE, session_name="acc-1"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="acc-1",
            session_path="acc-1",
            status="alive",
            is_temporary=False,
            user_id=1,
            phone=MX_PHONE,
        ),
    )
    await warming.add_channels(AddChannelsRequest(raw="@noticias_mx"))
    await _set_settings(chat=False, reactions=False, key="")
    recorder = _Recorder()
    telegram = FakeTelegram()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(_seams, "execute_owned_read", telegram.owned_read)
    monkeypatch.setattr(settings.warming, "self_search_enabled", True)
    real = _cycle.compute_intensity

    def warming_phase(age_hours: float) -> WarmingIntensity:
        return real(age_hours).model_copy(update={"phase": "warming"})

    monkeypatch.setattr(_cycle, "compute_intensity", warming_phase)
    return recorder, telegram


def _joined(recorder: _Recorder) -> list[str]:
    return [
        getattr(action, "channel", "")
        for _account, action in recorder.actions
        if action.action_type == "join_channel"
    ]


@pytest.mark.asyncio
async def test_the_visit_searches_then_joins_its_find_first(
    visit: tuple[_Recorder, FakeTelegram],
) -> None:
    recorder, telegram = visit

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert telegram.reads
    assert _joined(recorder)[0] == "futbol_mx"
    # The search reads are on the visit's bill like every other action.
    assert result.attempted_actions >= len(telegram.reads) + len(recorder.actions) - 1


@pytest.mark.asyncio
async def test_a_spent_join_cap_still_reads_but_never_joins_the_find(
    visit: tuple[_Recorder, FakeTelegram], monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder, telegram = visit
    monkeypatch.setattr(
        settings.warming,
        "phase_joins_per_day_max",
        dict.fromkeys(settings.warming.phase_joins_per_day_max, 0),
    )

    await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert telegram.reads
    assert _joined(recorder) == []


@pytest.mark.asyncio
async def test_a_search_flood_ends_the_visit(visit: tuple[_Recorder, FakeTelegram]) -> None:
    recorder, telegram = visit
    telegram.flood_on = "search_channels"

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "flood_wait"
    # Online, then offline: nothing was read or joined after the limit.
    assert recorder.types() == ["set_online", "set_online"]


@pytest.mark.asyncio
async def test_a_retired_generation_never_starts_a_search_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []

    async def gateway(account_id: str, _action: object) -> object:
        called.append(account_id)
        return object()

    monkeypatch.setattr(_seams, "_gateway_execute_read", gateway)
    _seams.activate_lease("acc-1", "run-2")
    with _seams.lease_scope("acc-1", "run-1"), pytest.raises(_seams.WarmingLeaseRevokedError):
        await _seams.execute_owned_read("acc-1", GetUserProfile())
    assert called == []


def test_its_own_finds_never_churn_out_of_its_interests() -> None:
    created = "2026-01-01T00:00:00+00:00"
    pool = [WarmingChannel(channel=f"c{i}", created_at=created, language="es") for i in range(40)]
    pool.append(WarmingChannel(channel="mine", created_at=created, language="es", found_by="acc-1"))

    for epoch in range(5):
        slice_ = _account_channel_affinity("acc-1", pool, epoch, "es")
        assert "mine" in {c.channel for c in slice_}
        assert len(slice_) < len(pool)
