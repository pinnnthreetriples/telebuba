"""The guard's safety-net steps: the key vault's own health, and the backup state it serves.

Real temp folders for the vault and the backup directory; the vault's HTTP answer is the only
thing replaced (``vault_health`` itself is covered in ``tests/core/test_passkey_vault.py``).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from core.config import settings
from core.db_snapshot import TIMESTAMP_FORMAT
from core.session_vault import new_vault_key, snapshot_session
from schemas.accounts import AccountCreate
from services import session_guard
from services.accounts import add_account
from services.session_guard import _loop, _vault_health

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _no_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    # A developer's .env may configure a real vault; the guard's health step must not ask it.
    monkeypatch.setattr(settings.passkey_vault, "token", "")


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    _loop.reset_for_tests()
    yield
    _loop.reset_for_tests()


def _script_vault(monkeypatch: pytest.MonkeyPatch, *states: str) -> list[dict[str, object]]:
    """The vault answers ``states`` in turn; the extras of each ``passkey_vault_unhealthy``."""
    answers = iter(states)
    journal: list[dict[str, object]] = []

    async def _health() -> str:
        return next(answers)

    async def _log(_level: str, event: str, **kwargs: object) -> None:
        assert event == "passkey_vault_unhealthy"
        assert "account_id" not in kwargs  # a vault-level fact names no account
        journal.append(cast("dict[str, object]", kwargs["extra"]))

    monkeypatch.setattr(_vault_health, "vault_health", _health)
    monkeypatch.setattr(_vault_health, "log_event", _log)
    return journal


@pytest.mark.asyncio
async def test_a_vault_that_starts_bad_is_journalled_once_not_every_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = _script_vault(monkeypatch, "sealed", "sealed", "sealed")

    for _ in range(3):
        await _vault_health.run_vault_health_check()

    assert journal == [{"state": "sealed"}]
    assert _vault_health.last_vault_state() == "sealed"


@pytest.mark.asyncio
async def test_a_change_to_a_bad_state_is_journalled_and_a_recovery_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = _script_vault(
        monkeypatch, "ok", "unreachable", "unreachable", "sealed", "ok", "rejected"
    )

    for _ in range(6):
        await _vault_health.run_vault_health_check()

    assert journal == [{"state": "unreachable"}, {"state": "sealed"}, {"state": "rejected"}]


@pytest.mark.asyncio
async def test_a_healthy_or_unused_vault_journals_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    journal = _script_vault(monkeypatch, "ok", "ok", "off", "off")

    for _ in range(4):
        await _vault_health.run_vault_health_check()

    assert journal == []


@pytest.mark.asyncio
async def test_the_pass_runs_the_vault_check_and_a_failing_one_never_ends_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []

    async def _log(_level: str, event: str, **kwargs: object) -> None:
        events.append((event, kwargs.get("extra")))

    async def _boom() -> None:
        msg = "unexpected"
        raise RuntimeError(msg)

    async def _none() -> int:
        return 0

    monkeypatch.setattr(_loop, "close_due_old_keys", _none)
    monkeypatch.setattr(_loop, "run_watch_pass", _none)
    monkeypatch.setattr(_loop, "run_backup_pass", _none)
    monkeypatch.setattr(_loop, "run_vault_health_check", _boom)
    monkeypatch.setattr(_loop, "log_event", _log)

    assert await session_guard.run_guard_pass() == 0
    assert events == [
        ("session_guard_failed", {"step": "passkey_vault", "error_type": "RuntimeError"})
    ]


@pytest.fixture
def backups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "db_backups"
    monkeypatch.setattr(settings.db, "backup_enabled", True)
    monkeypatch.setattr(settings.db, "backup_dir", directory)
    monkeypatch.setattr(settings.db, "backup_interval_hours", 24.0)
    return directory


def _db_backup(directory: Path, taken: datetime) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"telebuba-{taken.strftime(TIMESTAMP_FORMAT)}.db").write_bytes(b"x")


def _session(name: str) -> Path:
    path = settings.telegram.session_dir / f"{name}.session"
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("create table if not exists sessions (auth_key blob)")
        db.execute("insert into sessions values (?)", (b"k",))
    return path


@pytest.mark.asyncio
async def test_the_status_counts_accounts_without_a_copy_and_dates_the_newest(
    monkeypatch: pytest.MonkeyPatch, backups: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings.session_vault, "key", new_vault_key())
    monkeypatch.setattr(settings.session_vault, "mirror_dir", tmp_path / "mirror")
    for account_id in ("acc-1", "acc-2"):
        await add_account(AccountCreate(account_id=account_id))
    moment = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
    assert snapshot_session(_session("acc-1"), now=moment) is not None
    _db_backup(backups, datetime.now(UTC) - timedelta(hours=3))
    _vault_health._state.last = "sealed"

    status = await session_guard.read_session_guard_status()

    backups_status = status.backups
    assert backups_status.session_vault_enabled is True
    assert backups_status.mirror_configured is True
    assert (backups_status.accounts_total, backups_status.accounts_without_copy) == (2, 1)
    assert backups_status.last_session_copy_at == moment.isoformat()
    assert backups_status.db_backup_enabled is True
    assert backups_status.last_db_backup_at is not None
    assert backups_status.db_backup_overdue is False
    assert backups_status.passkey_vault == "sealed"


@pytest.mark.asyncio
@pytest.mark.usefixtures("backups")
async def test_with_the_vault_off_every_account_is_without_a_copy() -> None:
    for account_id in ("acc-1", "acc-2", "acc-3"):
        await add_account(AccountCreate(account_id=account_id))

    status = (await session_guard.read_session_guard_status()).backups

    assert status.session_vault_enabled is False
    assert status.mirror_configured is False
    assert (status.accounts_total, status.accounts_without_copy) == (3, 3)
    assert status.last_session_copy_at is None
    assert status.passkey_vault == "off"  # never checked, and no token configured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("age_hours", "overdue"),
    [(None, True), (47, False), (49, True)],
)
async def test_the_database_backup_is_overdue_after_two_intervals_or_when_there_is_none(
    backups: Path,
    age_hours: float | None,
    overdue: bool,  # noqa: FBT001 - a parametrized expectation
) -> None:
    if age_hours is not None:
        _db_backup(backups, datetime.now(UTC) - timedelta(hours=age_hours))

    status = (await session_guard.read_session_guard_status()).backups

    assert status.db_backup_overdue is overdue
    assert (status.last_db_backup_at is None) is (age_hours is None)


@pytest.mark.asyncio
@pytest.mark.usefixtures("backups")
async def test_disabled_database_backups_are_reported_as_disabled_not_overdue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.db, "backup_enabled", False)

    status = (await session_guard.read_session_guard_status()).backups

    assert (status.db_backup_enabled, status.db_backup_overdue) == (False, False)
