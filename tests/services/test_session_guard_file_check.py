"""The guard's start-up look: session files that are gone, and files no account owns."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from core.config import settings
from core.db import update_account_from_session_check
from schemas.accounts import AccountCreate
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import add_account
from services.session_guard import _file_check, _loop
from tests.session_files import write_session_file

if TYPE_CHECKING:
    from collections.abc import Iterator

Event = tuple[str, str, str | None, dict[str, object]]


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    _loop.reset_for_tests()
    yield
    _loop.reset_for_tests()


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[Event]:
    seen: list[Event] = []

    async def record(
        level: str, event: str, *, account_id: str | None = None, extra: dict | None = None
    ) -> None:
        seen.append((level, event, account_id, extra or {}))

    monkeypatch.setattr(_file_check, "log_event", record)
    return seen


async def _account(account_id: str, status: str) -> None:
    await add_account(AccountCreate(account_id=account_id, session_name=account_id))
    if status != "new":
        await update_account_from_session_check(
            TelegramSessionCheckResult(
                account_id=account_id,
                session_path=account_id,
                status=cast("str", status),  # ty: ignore[invalid-argument-type]
                is_temporary=False,
            )
        )


@pytest.mark.asyncio
async def test_missing_and_keyless_files_are_told_and_nothing_is_created(
    events: list[Event],
) -> None:
    folder = settings.telegram.session_dir
    await _account("kept", "alive")
    write_session_file(folder / "kept.session")
    await _account("lost", "alive")
    await _account("junk", "frozen")
    write_session_file(folder / "junk.session", key=None)
    await _account("placeholder", "new")  # never signed in: no file expected
    await _account("wiped", "unauthorized")  # reset on purpose: no file expected
    before = sorted(path.name for path in folder.glob("*"))

    await _file_check.run_file_reconciliation()

    told = sorted(
        account for _l, event, account, _x in events if event == "account_session_file_missing"
    )
    assert told == ["junk", "lost"]
    assert sorted(path.name for path in folder.glob("*")) == before
    assert all(level == "WARNING" and extra == {} for level, _e, _a, extra in events)


@pytest.mark.asyncio
async def test_files_no_account_owns_are_counted_once_without_names(events: list[Event]) -> None:
    folder = settings.telegram.session_dir
    await _account("owner", "alive")
    write_session_file(folder / "owner.session")
    for name in ("stray1", "stray2", "owner_rekey"):  # a re-key scratch is nobody's orphan
        write_session_file(folder / f"{name}.session")
    (folder / "owner.session.rekey-aside").write_bytes(b"x")

    await _file_check.run_file_reconciliation()

    assert [(e, a, x) for _l, e, a, x in events] == [
        ("session_orphan_files_found", None, {"count": 2})
    ]


@pytest.mark.asyncio
async def test_it_runs_once_per_start(events: list[Event]) -> None:
    await _account("lost", "alive")

    await _file_check.run_file_reconciliation()
    await _file_check.run_file_reconciliation()

    assert len(events) == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("events")
async def test_a_failing_look_is_retried_by_the_next_pass_and_never_ends_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    real = _file_check.list_accounts

    async def flaky() -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            msg = "database is locked"
            raise RuntimeError(msg)
        return await real()

    failures: list[str] = []

    async def record_failure(step: str, _exc: BaseException) -> None:
        failures.append(step)

    monkeypatch.setattr(_file_check, "list_accounts", flaky)
    monkeypatch.setattr(_loop, "_log_failure", record_failure)
    await _loop._guarded("session_files", _file_check.run_file_reconciliation)
    await _loop._guarded("session_files", _file_check.run_file_reconciliation)

    assert failures == ["session_files"]
    assert calls == 2
