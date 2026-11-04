"""Session audit persistence: one snapshot per account, a history row per audit."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.db import configure_database, create_account
from core.repositories.accounts import _delete_account
from core.repositories.session_audits import (
    fetch_last_session_audit_event,
    fetch_session_audit,
    list_foreign_session_counts,
    list_session_audit_times,
    save_session_audit,
)
from schemas.accounts import AccountCreate
from schemas.session_audit import AccountSession, AccountSessionAudit, AuthorizationList

if TYPE_CHECKING:
    from pathlib import Path


def _session(hash_: str, *, ours: bool) -> AccountSession:
    return AccountSession(
        hash=hash_,
        is_current=ours,
        is_ours=ours,
        official_app=False,
        device_model="Model",
        platform="Windows",
        system_version="10",
        app_name="Telegram Desktop",
        ip="192.0.2.10",
        country="Nowhere",
        date_created="2026-10-31T08:30:00+00:00",
        date_active="2026-11-01T08:30:00+00:00",
    )


def _audit(
    account_id: str = "acc-a", *, checked_at: str = "2026-11-02T10:00:00+00:00", foreign: int = 1
) -> AccountSessionAudit:
    sessions = [
        _session("-5", ours=True),
        *(_session(str(100 + i), ours=False) for i in range(foreign)),
    ]
    return AccountSessionAudit.of(
        account_id, checked_at, AuthorizationList(sessions=sessions, ttl_days=180)
    )


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


@pytest.mark.asyncio
async def test_a_saved_audit_reads_back_whole() -> None:
    await create_account(AccountCreate(account_id="acc-a"))
    assert await fetch_session_audit("acc-a") is None

    await save_session_audit(_audit())

    assert await fetch_session_audit("acc-a") == _audit()


@pytest.mark.asyncio
async def test_a_second_audit_replaces_the_snapshot_and_extends_the_history() -> None:
    await create_account(AccountCreate(account_id="acc-a"))
    await save_session_audit(_audit(foreign=2))
    await save_session_audit(
        _audit(checked_at="2026-11-03T10:00:00+00:00", foreign=0), killed_count=2
    )

    snapshot = await fetch_session_audit("acc-a")
    last = await fetch_last_session_audit_event("acc-a")

    assert snapshot is not None
    assert (snapshot.checked_at, snapshot.total, snapshot.foreign_count) == (
        "2026-11-03T10:00:00+00:00",
        1,
        0,
    )
    assert last is not None
    assert (last.checked_at, last.total, last.foreign_count, last.killed_count) == (
        "2026-11-03T10:00:00+00:00",
        1,
        0,
        2,
    )


@pytest.mark.asyncio
async def test_an_account_never_audited_has_no_history() -> None:
    assert await fetch_last_session_audit_event("acc-a") is None


@pytest.mark.asyncio
async def test_the_foreign_counts_are_listed_per_audited_account() -> None:
    for account_id in ("acc-a", "acc-b", "acc-c"):
        await create_account(AccountCreate(account_id=account_id))
    await save_session_audit(_audit("acc-a", foreign=3))
    await save_session_audit(_audit("acc-b", foreign=0))

    assert await list_foreign_session_counts() == {"acc-a": 3, "acc-b": 0}


@pytest.mark.asyncio
async def test_an_audit_without_its_time_is_refused() -> None:
    with pytest.raises(ValueError, match="time it was read"):
        await save_session_audit(AccountSessionAudit.empty("acc-a"))


@pytest.mark.asyncio
async def test_deleting_the_account_removes_its_snapshot_and_history_only() -> None:
    await create_account(AccountCreate(account_id="acc-a"))
    await create_account(AccountCreate(account_id="acc-b"))
    await save_session_audit(_audit("acc-a"))
    await save_session_audit(_audit("acc-b"))

    await asyncio.to_thread(_delete_account, "acc-a")

    assert await fetch_session_audit("acc-a") is None
    assert await fetch_last_session_audit_event("acc-a") is None
    assert await fetch_session_audit("acc-b") is not None
    assert await fetch_last_session_audit_event("acc-b") is not None


@pytest.mark.asyncio
async def test_the_audit_times_are_listed_per_audited_account() -> None:
    for account_id in ("acc-a", "acc-b"):
        await create_account(AccountCreate(account_id=account_id))
    await save_session_audit(_audit("acc-a", checked_at="2026-11-02T10:00:00+00:00"))
    await save_session_audit(_audit("acc-a", checked_at="2026-11-03T10:00:00+00:00"))

    assert await list_session_audit_times() == {"acc-a": "2026-11-03T10:00:00+00:00"}
