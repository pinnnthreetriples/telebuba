"""A transport hiccup never rewrites the key state; the re-check; QR same-user; key-gone sets."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    configure_database,
    fetch_account,
    update_account_from_session_check,
    update_account_status,
)
from schemas.accounts import AccountCreate, AccountRead
from schemas.qr_login import QrLoginState
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import add_account
from services.accounts import login as login_service
from services.accounts import qr_login as qr_service
from services.session_guard import _loop, _recheck, _watch

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_TRANSPORT = {"flood_wait", "network_error", "proxy_error", "unknown_error"}


@pytest.fixture(autouse=True)
def _db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setattr(settings.telegram, "session_dir", sessions)
    monkeypatch.setattr(settings.session_vault, "quarantine_dir", tmp_path / "quarantine")
    qr_service._FINISHED.clear()
    qr_service._FINISH_LOCKS.clear()
    _recheck.reset_for_tests()
    _watch.reset_for_tests()
    yield
    qr_service._FINISHED.clear()
    qr_service._FINISH_LOCKS.clear()


def _result(status: str, *, user_id: int | None = None) -> TelegramSessionCheckResult:
    return TelegramSessionCheckResult(
        account_id="acc",
        session_path="x",
        status=status,  # ty: ignore[invalid-argument-type]
        is_temporary=status in _TRANSPORT,
        user_id=user_id,
    )


async def _seed(status: str = "alive") -> None:
    await add_account(AccountCreate(account_id="acc", session_name="acc"))
    await update_account_from_session_check(_result(status, user_id=111))


@pytest.mark.asyncio
async def test_a_temporary_result_keeps_the_key_state_and_notes_the_link() -> None:
    await _seed()
    saved = await update_account_from_session_check(_result("proxy_error"))
    assert (saved.status, saved.link_status) == ("alive", "proxy_error")
    assert saved.link_checked_at is not None

    saved = await update_account_from_session_check(_result("alive", user_id=111))
    assert (saved.status, saved.link_status) == ("alive", None)

    await update_account_from_session_check(_result("flood_wait"))
    saved = await update_account_from_session_check(_result("unauthorized"))
    assert (saved.status, saved.link_status) == ("unauthorized", None)


@pytest.mark.asyncio
async def test_a_legacy_transport_status_is_replaced_by_the_next_real_verdict() -> None:
    await add_account(AccountCreate(account_id="acc", session_name="acc"))
    await update_account_status("acc", status="proxy_error")
    saved = await update_account_from_session_check(_result("alive", user_id=111))
    assert saved.status == "alive"


def test_session_error_is_not_a_gone_key() -> None:
    assert "session_error" not in login_service.KEY_GONE_STATUSES
    assert "session_error" not in qr_service._DEAD_STATUSES


@pytest.mark.asyncio
async def test_qr_is_refused_on_an_unprobeable_account() -> None:
    await _seed("session_error")
    with pytest.raises(qr_service.QrLoginError) as refused:
        await qr_service.begin_qr_login("acc")
    assert refused.value.code == "session_not_dead"


# --- the re-check -------------------------------------------------------------------------


def _row(account_id: str, **fields: object) -> AccountRead:
    base: dict[str, object] = {
        "account_id": account_id,
        "status": "alive",
        "link_status": "proxy_error",
        "proxy_id": f"p-{account_id}",
        "created_at": "t",
        "updated_at": "t",
    }
    return AccountRead(**{**base, **fields})  # ty: ignore[invalid-argument-type]


def _scene(
    monkeypatch: pytest.MonkeyPatch, rows: list[AccountRead], answers: list[object]
) -> list[str]:
    checked: list[str] = []

    async def _list() -> object:
        return SimpleNamespace(accounts=rows)

    async def _check(request: object) -> AccountRead:
        checked.append(request.account_id)  # ty: ignore[unresolved-attribute]
        answer = answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer  # ty: ignore[invalid-return-type]

    monkeypatch.setattr(_recheck, "list_accounts", _list)
    monkeypatch.setattr(_recheck, "check_account_session", _check)
    monkeypatch.setattr(_recheck, "is_lent", lambda account_id: account_id == "lent")
    return checked


@pytest.mark.asyncio
async def test_the_recheck_picks_one_failed_link_and_logs_when_it_is_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        _row("fine", link_status=None),
        _row("noproxy", proxy_id=None),
        _row("lent"),
        _row("legacy", status="proxy_error", link_status=None),
    ]
    checked = _scene(monkeypatch, rows, [_row("legacy", link_status=None, status="alive")])
    events: list[str] = []

    async def _log(_level: str, event: str, **_kw: object) -> None:
        events.append(event)

    monkeypatch.setattr(_recheck, "log_event", _log)
    assert await _recheck.run_recheck_pass() == "legacy"
    assert checked == ["legacy"]
    assert events == ["account_link_restored"]


@pytest.mark.asyncio
async def test_a_failed_recheck_backs_off_and_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_row("a"), _row("b")]
    checked = _scene(monkeypatch, rows, [ValueError("x"), _row("b", link_status=None), _row("a")])
    start = datetime(2026, 1, 1, tzinfo=UTC)
    assert await _recheck.run_recheck_pass(now=start) == "a"  # raised: counted, not raised
    # Another proxy, so no spacing; "a" waits 15 minutes, "b" goes first.
    assert await _recheck.run_recheck_pass(now=start + timedelta(seconds=1)) == "b"
    rows.pop()  # "b" is restored
    after = start + timedelta(minutes=31)  # past both the 15 minute wait and the proxy spacing
    assert await _recheck.run_recheck_pass(now=after) == "a"  # failed again: next wait is 30
    assert _recheck._state.next_at["a"] == after + timedelta(minutes=30)
    assert checked == ["a", "b", "a"]


@pytest.mark.asyncio
async def test_the_recheck_keeps_the_proxy_spacing(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_row("a", proxy_id="shared"), _row("b", proxy_id="shared")]
    checked = _scene(monkeypatch, rows, [_row("a", link_status=None), _row("b")])
    start = datetime(2026, 1, 1, tzinfo=UTC)
    await _recheck.run_recheck_pass(now=start)
    rows.pop(0)  # "a" is restored
    assert await _recheck.run_recheck_pass(now=start + timedelta(seconds=5)) is None
    spacing = settings.session_audit.watch_proxy_spacing_seconds
    assert await _recheck.run_recheck_pass(now=start + timedelta(seconds=spacing)) == "b"
    assert checked == ["a", "b"]


@pytest.mark.asyncio
async def test_the_guard_pass_runs_the_recheck_step(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []

    async def _recheck_step() -> None:
        ran.append("recheck")

    async def _nothing() -> int:
        return 0

    for name in (
        "repair_key_intents",
        "close_due_old_keys",
        "run_watch_pass",
        "run_backup_pass",
        "run_vault_health_check",
        "run_file_reconciliation",
    ):
        monkeypatch.setattr(_loop, name, _nothing)
    monkeypatch.setattr(_loop, "run_recheck_pass", _recheck_step)
    await _loop.run_guard_pass()
    assert ran == ["recheck"]


# --- QR: the same Telegram user ------------------------------------------------------------


async def _qr_scene(monkeypatch: pytest.MonkeyPatch, scanned_user: int) -> Path:
    await _seed("alive")
    await update_account_from_session_check(_result("unauthorized"))
    file = settings.telegram.session_dir / "acc.session"
    file.write_bytes(b"SQLite format 3\x00key")
    state = QrLoginState(
        account_id="acc",
        status="alive",
        result=TelegramSessionCheckResult(
            account_id="acc",
            session_path=str(settings.telegram.session_dir / "acc"),
            status="alive",
            is_temporary=False,
            user_id=scanned_user,
            phone="70000000000",
        ),
    )
    monkeypatch.setattr(qr_service, "read_qr_login", lambda _: state)
    monkeypatch.setattr(qr_service, "forget_qr_login", lambda _: None)

    async def _log_out(_request: object, *, wipe_session: bool) -> TelegramSessionCheckResult:
        assert wipe_session
        return TelegramSessionCheckResult(
            account_id="acc",
            session_path="x",
            status="network_error",
            is_temporary=True,
            error_type="TimeoutError",
        )

    monkeypatch.setattr(qr_service, "log_out_session", _log_out)
    return file


@pytest.mark.asyncio
async def test_another_user_scanning_leaves_the_row_alone_and_quarantines_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file = await _qr_scene(monkeypatch, scanned_user=222)
    events: list[str] = []
    real_log = qr_service.log_event

    async def _log(level: str, event: str, **kw: object) -> None:
        events.append(event)
        await real_log(level, event, **kw)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(qr_service, "log_event", _log)

    with pytest.raises(qr_service.QrLoginError) as refused:
        await qr_service.get_qr_login("acc")

    assert refused.value.code == "qr_user_mismatch"
    row = await fetch_account("acc")
    assert row is not None
    assert (row.status, row.user_id, row.phone) == ("unauthorized", 111, None)
    assert not file.exists()
    assert len(list(settings.session_vault.quarantine_dir.iterdir())) == 1
    assert "qr_login_user_mismatch" in events


@pytest.mark.asyncio
async def test_the_same_user_scanning_signs_the_account_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _qr_scene(monkeypatch, scanned_user=111)

    async def _inbox(_account_id: str) -> None:
        return None

    monkeypatch.setattr(qr_service, "start_account_inbox", _inbox)
    view = await qr_service.get_qr_login("acc")
    assert view.account is not None
    assert (view.account.status, view.account.user_id) == ("alive", 111)
