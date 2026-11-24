"""Adversarial cases of the sign-in guard and of "close all foreign" (shown vs unseen)."""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from core.repositories.session_audits import fetch_session_audit, save_session_audit
from core.telegram_client import account_auth_lock
from schemas.accounts import AccountRead
from schemas.session_audit import AccountSession, AccountSessionAudit, AuthorizationList
from schemas.session_close import SessionCloseOutcome
from services import _account_owner
from services.accounts import AccountActionError, AccountNotFoundError
from services.accounts import session_audit as audit_svc
from services.accounts import session_close as svc
from services.session_guard import _watch

if TYPE_CHECKING:
    from collections.abc import Iterator

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def _account() -> AccountRead:
    return AccountRead.model_validate(
        {
            "account_id": "acc-1",
            "status": "alive",
            "created_at": "2026-10-31T00:00:00+00:00",
            "updated_at": "2026-10-31T00:00:00+00:00",
            "proxy_id": "proxy-1",
            "devices": ["desktop"],
        }
    )


def _session(hash_: str, *, ours: bool = False) -> AccountSession:
    return AccountSession(
        hash=hash_,
        is_current=hash_ == "1",
        is_ours=ours or hash_ == "1",
        official_app=False,
        device_model="Model",
        platform="Windows",
        system_version="10",
        app_name="Telegram Desktop",
        ip="192.0.2.10",
        country="Nowhere",
    )


class _Wire:
    def __init__(self) -> None:
        self.listing: list[AccountSession] = []
        self.reads = 0
        self.closes: list[list[str]] = []


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> _Wire:
    state = _Wire()

    async def _require(_account_id: str) -> AccountRead:
        return _account()

    async def _read(_a: str, _d: list[str], _own: object = None) -> AuthorizationList:
        state.reads += 1
        return AuthorizationList(sessions=list(state.listing))

    async def _close(
        _a: str, _d: list[str], hashes: list[str], _p: list[float], _own: object = None
    ) -> list[SessionCloseOutcome]:
        state.closes.append(list(hashes))
        return [SessionCloseOutcome(hash=h, status="closed") for h in hashes]

    monkeypatch.setattr(audit_svc, "require_account", _require)
    monkeypatch.setattr(audit_svc, "read_account_sessions", _read)
    monkeypatch.setattr(svc, "close_sessions", _close)
    return state


async def _save(*sessions: AccountSession) -> None:
    await save_session_audit(
        AccountSessionAudit(
            account_id="acc-1",
            checked_at="2026-11-01T00:00:00+00:00",
            total=len(sessions),
            foreign_count=sum(1 for s in sessions if not s.is_ours),
            sessions=list(sessions),
        )
    )


@pytest.mark.asyncio
async def test_a_session_marked_ours_in_the_saved_audit_is_not_closed_when_it_turns_foreign(
    wire: _Wire,
) -> None:
    # A manual "ours" mark that was dropped (or a recorded web login that is no longer
    # known): the operator was shown it as ours, so a bulk close must not end it.
    await _save(_session("1"), _session("5", ours=True), _session("2"))
    wire.listing = [_session("1"), _session("5"), _session("2")]

    result = await svc.close_account_foreign_sessions("acc-1")

    assert wire.closes == [["2"]]
    assert (result.closed_count, result.unseen_count, result.no_prior_audit) == (1, 1, False)


@pytest.mark.asyncio
async def test_a_very_old_saved_audit_still_gates_by_hash(wire: _Wire) -> None:
    await save_session_audit(
        AccountSessionAudit(
            account_id="acc-1",
            checked_at="2020-01-01T00:00:00+00:00",
            total=2,
            foreign_count=1,
            sessions=[_session("1"), _session("2")],
        )
    )
    wire.listing = [_session("1"), _session("2"), _session("9")]

    result = await svc.close_account_foreign_sessions("acc-1")

    assert wire.closes == [["2"]]
    assert result.unseen_count == 1


@pytest.mark.asyncio
async def test_a_foreign_session_that_vanished_since_the_saved_audit_is_not_asked_for(
    wire: _Wire,
) -> None:
    await _save(_session("1"), _session("2"), _session("3"))
    wire.listing = [_session("1"), _session("3")]

    result = await svc.close_account_foreign_sessions("acc-1")

    assert wire.closes == [["3"]]
    assert result.unseen_count == 0


@pytest.mark.asyncio
async def test_nothing_is_closed_when_everything_foreign_is_new(wire: _Wire) -> None:
    await _save(_session("1"))
    wire.listing = [_session("1"), _session("2"), _session("3")]

    result = await svc.close_account_foreign_sessions("acc-1")

    assert wire.closes == []
    assert (result.closed_count, result.unseen_count, result.no_prior_audit) == (0, 2, False)
    saved = await fetch_session_audit("acc-1")
    assert saved is not None
    assert [s.hash for s in saved.sessions if not s.is_ours] == ["2", "3"]
    # The next click closes them: they were shown by now.
    again = await svc.close_account_foreign_sessions("acc-1")
    assert wire.closes == [["2", "3"]]
    assert again.unseen_count == 0


@pytest.mark.asyncio
async def test_a_sign_in_that_starts_while_the_close_waits_for_the_audit_lock_refuses_it(
    wire: _Wire,
) -> None:
    await _save(_session("1"), _session("2"))
    wire.listing = [_session("1"), _session("2")]
    async with audit_svc.account_audit_lock("acc-1"):
        waiting = asyncio.create_task(svc.close_account_foreign_sessions("acc-1"))
        await asyncio.sleep(0)
        # A sign-in begins and is still running when the audit lock frees.
        lock = account_auth_lock("acc-1")
        await lock.acquire()
    try:
        with pytest.raises(AccountActionError) as refused:
            await waiting
    finally:
        lock.release()

    assert refused.value.code == "sign_in_in_progress"
    assert (wire.reads, wire.closes) == (0, [])


@pytest.mark.asyncio
@pytest.mark.usefixtures("wire")
async def test_the_guard_does_not_queue_behind_the_auth_lock() -> None:
    async with account_auth_lock("acc-1"):
        # Returns at once (a QR sign-in holds the lock for minutes); never waits for it.
        with pytest.raises(AccountActionError):
            await asyncio.wait_for(svc.close_account_sessions("acc-1", ["2"]), timeout=1)
        with pytest.raises(AccountActionError):
            await asyncio.wait_for(audit_svc.audit_account_sessions("acc-1"), timeout=1)
    # And the audit lock is not left held by the refusal.
    assert not audit_svc.account_audit_lock("acc-1").locked()


@pytest.mark.asyncio
@pytest.mark.usefixtures("wire")
async def test_an_unknown_account_is_not_found_not_refused_as_signing_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _gone(account_id: str) -> AccountRead:
        raise AccountNotFoundError(account_id)

    monkeypatch.setattr(audit_svc, "require_account", _gone)
    async with account_auth_lock("acc-1"):
        with pytest.raises(AccountNotFoundError):
            await svc.close_account_sessions("acc-1", ["2"])


class _WatchWire:
    def __init__(self) -> None:
        self.checked: list[str] = []


@pytest.fixture
def watch(monkeypatch: pytest.MonkeyPatch) -> Iterator[_WatchWire]:
    state = _WatchWire()
    _watch.reset_for_tests()
    _account_owner.reset_for_tests()
    monkeypatch.setattr(_watch.settings.session_audit, "watch_startup_delay_seconds", 0.0)
    monkeypatch.setattr(_watch, "_rng", random.Random(7))  # noqa: S311 - a seeded test draw

    async def _list() -> SimpleNamespace:
        return SimpleNamespace(accounts=[_account()])

    async def _times() -> dict[str, str]:
        return {}

    async def _check(request: object) -> None:
        state.checked.append(request.account_id)  # ty: ignore[unresolved-attribute]

    async def _noop(*_a: object, **_k: object) -> None:
        return None

    async def _no_passkey(_account_id: str) -> None:
        return None

    monkeypatch.setattr(_watch, "list_accounts", _list)
    monkeypatch.setattr(_watch, "list_session_audit_times", _times)
    monkeypatch.setattr(_watch, "check_account_session", _check)
    monkeypatch.setattr(_watch, "fetch_account_passkey", _no_passkey)
    monkeypatch.setattr(_watch, "check_passkey_vault", _noop)
    yield state
    _account_owner.reset_for_tests()


@pytest.mark.asyncio
async def test_the_watch_waits_out_a_sign_in_without_treating_it_as_a_dead_session(
    wire: _Wire, watch: _WatchWire
) -> None:
    wire.listing = [_session("1"), _session("2")]

    async with account_auth_lock("acc-1"):
        assert await _watch.run_watch_pass(now=NOW) == "acc-1"
        assert watch.checked == []  # not a dead session: no session check
        assert wire.reads == 0  # no Telegram read, hence no false alert
        assert await fetch_session_audit("acc-1") is None
        # Retried after the short wait, not a whole interval.
        assert _watch._state.due["acc-1"] == NOW + timedelta(hours=1)

    # Not due before the retry wait, due after it; then the session is news, once.
    assert await _watch.run_watch_pass(now=NOW + timedelta(minutes=30)) is None
    assert await _watch.run_watch_pass(now=NOW + timedelta(hours=1, minutes=1)) == "acc-1"
    assert wire.reads == 1
    saved = await fetch_session_audit("acc-1")
    assert saved is not None
    assert saved.foreign_count == 1
