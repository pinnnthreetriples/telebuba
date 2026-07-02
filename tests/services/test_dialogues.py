"""Tests for ``services.dialogues`` — acquaintance-pair assignment."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    configure_database,
    create_account,
    latest_unreplied_for,
    record_dialogue_message,
    try_claim_message_reply,
    update_account_from_session_check,
    upsert_warming_state,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate, AccountStatus
from schemas.telegram_session import TelegramSessionCheckResult
from schemas.warming import WarmingStateWrite
from services.dialogues import assign_pairs, get_partners

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


async def _seed_warming(account_id: str, status: AccountStatus = "new") -> None:
    await create_account(AccountCreate(account_id=account_id))
    if status != "new":
        await update_account_from_session_check(
            TelegramSessionCheckResult(
                account_id=account_id,
                session_path=account_id,
                status=status,
                is_temporary=False,
            ),
        )
    await upsert_warming_state(WarmingStateWrite(account_id=account_id, state="active"))


@pytest.mark.asyncio
async def test_assign_pairs_needs_two_accounts() -> None:
    await _seed_warming("acc-1")
    assert (await assign_pairs()).pairs == []


@pytest.mark.asyncio
async def test_assign_pairs_creates_mutual_partners() -> None:
    for i in (1, 2, 3):
        await _seed_warming(f"acc-{i}")

    result = await assign_pairs()

    assert result.pairs
    partners = (await get_partners("acc-1")).partners
    assert partners
    assert set(partners) <= {"acc-2", "acc-3"}
    # pairing is symmetric
    assert "acc-1" in (await get_partners(partners[0])).partners


@pytest.mark.asyncio
async def test_assign_pairs_excludes_dead_accounts() -> None:
    await _seed_warming("acc-1")
    await _seed_warming("acc-2")
    await _seed_warming("acc-dead", status="account_error")

    await assign_pairs()

    assert (await get_partners("acc-dead")).partners == []
    assert set((await get_partners("acc-1")).partners) <= {"acc-2"}


@pytest.mark.asyncio
async def test_assign_pairs_is_stable_without_change() -> None:
    for i in (1, 2, 3):
        await _seed_warming(f"acc-{i}")

    first = (await assign_pairs()).pairs
    again = (await assign_pairs()).pairs

    def _key(pairs: list) -> set[tuple[str, str]]:
        return {(pair.account_a, pair.account_b) for pair in pairs}

    assert _key(first) == _key(again)
    assert first[0].assigned_at == again[0].assigned_at


@pytest.mark.asyncio
async def test_assign_pairs_reshuffles_when_membership_changes() -> None:
    await _seed_warming("acc-1")
    await _seed_warming("acc-2")
    await assign_pairs()

    await _seed_warming("acc-3")
    pairs = (await assign_pairs()).pairs

    covered = {pair.account_a for pair in pairs} | {pair.account_b for pair in pairs}
    assert covered == {"acc-1", "acc-2", "acc-3"}


@pytest.mark.asyncio
async def test_try_claim_message_reply_atomic() -> None:
    """First claim wins; the second one (race) returns False without sending."""
    await create_account(AccountCreate(account_id="a"))
    await create_account(AccountCreate(account_id="b"))
    await record_dialogue_message("a", "b", "hi")
    incoming = await latest_unreplied_for("b")
    assert incoming is not None

    assert await try_claim_message_reply(incoming.id) is True
    assert await try_claim_message_reply(incoming.id) is False
    assert await latest_unreplied_for("b") is None
