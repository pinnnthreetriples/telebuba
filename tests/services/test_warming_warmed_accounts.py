"""Tests for ``services.warming.list_warmed_accounts`` (neurocomment overview seam)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    configure_database,
    create_account,
    mark_promoted_to_nc,
    upsert_warming_state,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate
from schemas.warming import WarmingStateWrite
from services.warming import list_warmed_accounts, load_board

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


def _days_ago(days: int) -> str:
    # +1 h so the whole-day floor lands exactly on ``days``.
    return (datetime.now(UTC) - timedelta(days=days, hours=1)).isoformat()


@pytest.mark.asyncio
async def test_list_warmed_accounts_includes_every_promoted_account() -> None:
    """Graduation, not the day count, gates the warmed pool — below-target included."""
    await create_account(AccountCreate(account_id="old", label="Old"))
    await create_account(AccountCreate(account_id="young", label="Young"))
    await create_account(AccountCreate(account_id="fresh", label="Fresh"))  # never warmed
    await upsert_warming_state(
        WarmingStateWrite(account_id="old", state="active", started_at=_days_ago(20)),
    )
    await upsert_warming_state(
        WarmingStateWrite(account_id="young", state="active", started_at=_days_ago(5)),
    )
    # Only the promoted ones appear (the un-promoted "fresh" stays out); the
    # below-target "young" is included because the operator graduated it.
    await mark_promoted_to_nc("old")
    await mark_promoted_to_nc("young")

    result = await list_warmed_accounts(14)

    assert [a.account_id for a in result.accounts] == ["old", "young"]
    assert result.accounts[0].label == "Old"


@pytest.mark.asyncio
async def test_list_warmed_accounts_sorted_newest_warmed_first() -> None:
    await create_account(AccountCreate(account_id="a", label="A"))
    await create_account(AccountCreate(account_id="b", label="B"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="a", state="active", started_at=_days_ago(30)),
    )
    await upsert_warming_state(
        WarmingStateWrite(account_id="b", state="active", started_at=_days_ago(15)),
    )
    await mark_promoted_to_nc("a")
    await mark_promoted_to_nc("b")

    result = await list_warmed_accounts(14)

    # Most-warmed first.
    assert [a.account_id for a in result.accounts] == ["a", "b"]


@pytest.mark.asyncio
async def test_below_target_graduation_lands_in_warmed_not_stranded() -> None:
    """An explicit «в прогретые» before the day target still lands in «Прогреты».

    It's the operator's call, not an accident — so the account shows in the
    warmed pool (never in «Готовы», never invisible in no column at all).
    """
    await create_account(AccountCreate(account_id="grad", label="Grad"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="grad", state="idle", started_at=_days_ago(1)),
    )
    await mark_promoted_to_nc("grad")

    # 1 day is below the 14-day target, but the graduation is honoured…
    assert [a.account_id for a in (await list_warmed_accounts(14)).accounts] == ["grad"]
    # …and it is NOT left in the ready/warming columns.
    board = await load_board()
    assert "grad" not in {c.account_id for c in board.idle}
    assert "grad" not in {c.account_id for c in board.warming}


@pytest.mark.asyncio
async def test_list_warmed_accounts_empty_when_none_warmed() -> None:
    await create_account(AccountCreate(account_id="fresh", label="Fresh"))
    result = await list_warmed_accounts(14)
    assert result.accounts == []


@pytest.mark.asyncio
async def test_list_warmed_accounts_skips_unpromoted_even_when_old_enough() -> None:
    """Crossing ``min_days`` alone no longer auto-graduates: the operator must promote."""
    await create_account(AccountCreate(account_id="patient", label="Patient"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="patient", state="active", started_at=_days_ago(40)),
    )

    result = await list_warmed_accounts(14)

    assert result.accounts == []


@pytest.mark.asyncio
async def test_promote_to_neurocomment_appears_in_warmed_list_after_threshold() -> None:
    """The card button stops warming and flips the flag; the warmed-list picks it up."""
    from services.warming import promote_to_neurocomment  # noqa: PLC0415

    await create_account(AccountCreate(account_id="graduate", label="Graduate"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="graduate", state="active", started_at=_days_ago(30)),
    )

    card = await promote_to_neurocomment("graduate")
    assert card.promoted_to_nc is True
    assert card.state == "idle"  # warming loop stopped

    result = await list_warmed_accounts(14)
    assert [a.account_id for a in result.accounts] == ["graduate"]

    # Regression: a promoted account must not linger in the "ready to warm"
    # kanban column just because its state also happens to be "idle" — the
    # operator moved it to the warmed pool, it shouldn't show as available.
    board = await load_board()
    assert "graduate" not in {c.account_id for c in board.idle}
    assert "graduate" not in {c.account_id for c in board.warming}


@pytest.mark.asyncio
async def test_warmed_account_carries_card_meta() -> None:
    """The warmed entry surfaces the card's proxy type/country + target days (de-mock)."""
    from core.db import update_proxy_check  # noqa: PLC0415
    from schemas.proxy import ProxyCheckUpdate  # noqa: PLC0415
    from services.warming import promote_to_neurocomment  # noqa: PLC0415
    from tests.factories import seed_account_proxy  # noqa: PLC0415

    await create_account(AccountCreate(account_id="meta", label="Meta"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="meta", state="active", started_at=_days_ago(20)),
    )
    proxy_id = await seed_account_proxy("meta")
    # A proxy check stamps the exit country; the warmed card renders its flag.
    await update_proxy_check(
        ProxyCheckUpdate(proxy_id=proxy_id, status="tcp_working", country_code="CO"),
    )
    await promote_to_neurocomment("meta")

    result = await list_warmed_accounts(14)

    assert len(result.accounts) == 1
    warmed = result.accounts[0]
    assert warmed.proxy_type == "socks5"
    assert warmed.proxy_country == "CO"
    assert warmed.target_days == 14


@pytest.mark.asyncio
async def test_board_card_carries_telegram_name() -> None:
    """The board card surfaces first_name/last_name + avatar_etag for the display name/photo."""
    from core.db import update_account_from_session_check  # noqa: PLC0415
    from schemas.telegram_session import TelegramSessionCheckResult  # noqa: PLC0415

    await create_account(AccountCreate(account_id="named"))
    updated = await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="named",
            session_path="named",
            status="alive",
            is_temporary=False,
            first_name="Alice",
            last_name="Smith",
            avatar_thumb=b"jpeg-bytes",
        ),
    )
    assert updated.avatar_etag is not None

    board = await load_board()

    card = next(c for c in board.idle if c.account_id == "named")
    assert card.first_name == "Alice"
    assert card.last_name == "Smith"
    # The card carries the avatar etag so it can render /avatar?v={etag}.
    assert card.avatar_etag == updated.avatar_etag


@pytest.mark.asyncio
async def test_warmed_account_carries_telegram_name() -> None:
    """list_warmed_accounts propagates the card's name + avatar_etag (de-id-mock)."""
    from core.db import update_account_from_session_check  # noqa: PLC0415
    from schemas.telegram_session import TelegramSessionCheckResult  # noqa: PLC0415
    from services.warming import promote_to_neurocomment  # noqa: PLC0415

    await create_account(AccountCreate(account_id="named"))
    updated = await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="named",
            session_path="named",
            status="alive",
            is_temporary=False,
            first_name="Alice",
            last_name="Smith",
            avatar_thumb=b"jpeg-bytes",
        ),
    )
    assert updated.avatar_etag is not None
    await upsert_warming_state(
        WarmingStateWrite(account_id="named", state="active", started_at=_days_ago(20)),
    )
    await promote_to_neurocomment("named")

    warmed = (await list_warmed_accounts(14)).accounts

    assert len(warmed) == 1
    assert warmed[0].first_name == "Alice"
    assert warmed[0].last_name == "Smith"
    assert warmed[0].avatar_etag == updated.avatar_etag


@pytest.mark.asyncio
async def test_unmark_neurocomment_removes_from_warmed_list() -> None:
    """The un-promote affordance flips the flag back; the warmed-list drops the account."""
    from services.warming import promote_to_neurocomment, unmark_neurocomment  # noqa: PLC0415

    await create_account(AccountCreate(account_id="returner", label="Returner"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="returner", state="active", started_at=_days_ago(30)),
    )
    await promote_to_neurocomment("returner")
    assert [a.account_id for a in (await list_warmed_accounts(14)).accounts] == ["returner"]

    card = await unmark_neurocomment("returner")

    assert card.promoted_to_nc is False
    assert (await list_warmed_accounts(14)).accounts == []


@pytest.mark.asyncio
async def test_handoff_flags_account_and_survives_in_warmed_list() -> None:
    """Second stage: «в нейрокомментинг» flips nc_handed_off but keeps the graduation."""
    from services.warming import handoff_to_neurocomment, promote_to_neurocomment  # noqa: PLC0415

    await create_account(AccountCreate(account_id="handed", label="Handed"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="handed", state="active", started_at=_days_ago(30)),
    )
    await promote_to_neurocomment("handed")

    card = await handoff_to_neurocomment("handed")

    assert card.promoted_to_nc is True
    assert card.nc_handed_off is True
    # Still in the raw warmed list — the split into the two UI columns is done by
    # the frontend on nc_handed_off, so the backend keeps returning the account.
    warmed = (await list_warmed_accounts(14)).accounts
    assert [a.account_id for a in warmed] == ["handed"]
    assert warmed[0].nc_handed_off is True


@pytest.mark.asyncio
async def test_handoff_rejects_account_not_graduated() -> None:
    """Handing off an un-graduated account is a caller error (400), not a silent flag."""
    from services.warming import handoff_to_neurocomment  # noqa: PLC0415

    await create_account(AccountCreate(account_id="raw", label="Raw"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="raw", state="idle", started_at=_days_ago(3)),
    )

    with pytest.raises(ValueError, match="not in the warmed pool"):
        await handoff_to_neurocomment("raw")


@pytest.mark.asyncio
async def test_unmark_neurocomment_clears_handoff_flag() -> None:
    """Un-promoting a handed-off account clears BOTH flags (no NC idle-pool leak)."""
    from services.warming import (  # noqa: PLC0415
        handoff_to_neurocomment,
        promote_to_neurocomment,
        unmark_neurocomment,
    )

    await create_account(AccountCreate(account_id="round", label="Round"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="round", state="active", started_at=_days_ago(30)),
    )
    await promote_to_neurocomment("round")
    await handoff_to_neurocomment("round")

    card = await unmark_neurocomment("round")

    assert card.promoted_to_nc is False
    assert card.nc_handed_off is False
    assert (await list_warmed_accounts(14)).accounts == []


@pytest.mark.asyncio
async def test_start_warming_clears_promotion_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dragging a graduated card back into warming must drop it from the NC pool (Bug 2)."""
    import asyncio  # noqa: PLC0415

    from core.db import save_warming_settings  # noqa: PLC0415
    from schemas.warming import StartWarmingRequest, StopWarmingRequest  # noqa: PLC0415
    from services.warming import (  # noqa: PLC0415
        _RUNTIME,
        _runtime,
        promote_to_neurocomment,
        start_warming,
        stop_warming,
    )

    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)

    await create_account(AccountCreate(account_id="boomerang", label="Boomerang"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="boomerang", state="active", started_at=_days_ago(30)),
    )
    # Disable readiness gate so start_warming proceeds without proxy/spam setup.
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )

    await promote_to_neurocomment("boomerang")
    assert [a.account_id for a in (await list_warmed_accounts(14)).accounts] == ["boomerang"]

    card = await start_warming(StartWarmingRequest(account_id="boomerang"))

    assert card.promoted_to_nc is False
    assert (await list_warmed_accounts(14)).accounts == []

    # Cleanup: cancel the background loop task so the test doesn't leak.
    await stop_warming(StopWarmingRequest(account_id="boomerang"))
    assert "boomerang" not in _RUNTIME
