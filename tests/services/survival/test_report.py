"""The survival panel and card badges over a synthetic journal and a real database."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import create_account, fetch_account, upsert_warming_state
from core.repositories.warming_daily_stats import add_warming_daily_stats
from schemas.accounts import AccountCreate
from schemas.warming import WarmingStateWrite
from schemas.warming_daily_stats import WarmingDailyStats
from services.survival import load_account_survivals, load_survival, report
from tests.services.survival._support import at, clean, iso, limited, released_life, row

if TYPE_CHECKING:
    from schemas.lifecycle import LifecycleEvent


def _journal(monkeypatch: pytest.MonkeyPatch, events: list[LifecycleEvent]) -> None:
    async def fake(account_ids: list[str] | None = None) -> list[LifecycleEvent]:
        if account_ids is None:
            return events
        return [event for event in events if event.account_id in account_ids]

    monkeypatch.setattr(report, "list_all_lifecycle_events", fake)


@pytest.mark.asyncio
async def test_the_panel_counts_cohorts_causes_and_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="live", label="Main"))
    events = [
        *released_life(14, account="live"),
        clean(22, account="live"),
        *released_life(14, account="gone"),
        row("session_dead", 16, account="gone", status="unauthorized"),
        row("deleted", 17, account="gone", reason="dead"),
        # Re-warmed: still one account in every number.
        row("warming_started", 18, account="live", stint=18, wave=2),
    ]
    _journal(monkeypatch, events)

    panel = await load_survival("week", now=at(25))

    warming, d7, _d30 = panel.windows
    assert (warming.alive, d7.alive, d7.dead) == (2, 1, 1)
    assert [(cause.cause, cause.count) for cause in panel.causes] == [("session_dead", 1)]
    assert [cohort.key for cohort in panel.cohorts] == ["2026-W37"]
    assert sorted(life.label for life in panel.lives) == ["Main", "gone"]
    main = next(life for life in panel.lives if life.label == "Main")
    assert [wave.wave for wave in main.waves] == [1, 2]
    assert panel.releases == 2
    assert panel.full_data_since == iso(0)[:10]


@pytest.mark.asyncio
async def test_cohorts_fall_back_to_the_live_warming_row(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="live"))
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="live",
            state="sleeping",
            started_at=iso(0),
            target_days=21,
            activity_persona="calm",
        )
    )
    _journal(monkeypatch, released_life(14, account="live"))

    by_length = await load_survival("length", now=at(25))
    by_persona = await load_survival("persona", now=at(25))

    assert [cohort.key for cohort in by_length.cohorts] == ["21"]
    assert [cohort.key for cohort in by_persona.cohorts] == ["calm"]


@pytest.mark.asyncio
async def test_the_start_row_wins_over_the_live_row(monkeypatch: pytest.MonkeyPatch) -> None:
    start = row("warming_started", 0, persona="active", target_days=7, proxy_country="DE")
    _journal(monkeypatch, [start, row("warmed", 7)])

    panel = await load_survival("proxy_country", now=at(25))

    assert [cohort.key for cohort in panel.cohorts] == ["DE"]


@pytest.mark.asyncio
async def test_an_empty_journal_is_an_empty_panel_with_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _journal(monkeypatch, [])
    day = at(25).date().isoformat()
    for account_id in ("a", "b"):
        await add_warming_daily_stats(
            WarmingDailyStats(account_id=account_id, day=day, cycles=2, reads=3)
        )

    panel = await load_survival(now=at(25))

    assert panel.lives == []
    assert panel.full_data_since is None
    assert [(point.day, point.accounts, point.cycles, point.reads) for point in panel.daily] == [
        (day, 2, 4, 6),
    ]


@pytest.mark.asyncio
async def test_the_card_badge_counts_days_since_release(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    account = await fetch_account("acc-1")
    assert account is not None
    born = account.model_copy(update={"created_at": iso(-1)})
    _journal(monkeypatch, [*released_life(14), row("quarantined", 15), clean(20)])

    survivals = await load_account_survivals([born], now=at(26.5))

    badge = survivals["acc-1"]
    assert (badge.days_since_release, badge.status, badge.wounds) == (12, "wounded", 1)


@pytest.mark.asyncio
async def test_a_reimported_id_does_not_wear_its_past_life(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    account = await fetch_account("acc-1")
    assert account is not None
    born = account.model_copy(update={"created_at": iso(30)})
    # The previous life's ``deleted`` row never landed (best-effort), so only the
    # account row's own birth separates the two lives.
    _journal(monkeypatch, [*released_life(14), limited(16)])

    assert await load_account_survivals([born], now=at(40)) == {}
