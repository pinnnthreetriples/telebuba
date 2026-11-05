"""Telebuba's own web login is ours: recorded from the listing, never foreign, never closed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio

from core.config import settings
from core.db import create_account
from core.repositories.account_own_authorizations import (
    fetch_own_authorizations,
    save_own_authorizations,
)
from core.repositories.account_rekeys import save_rekey
from core.repositories.session_audits import fetch_session_audit
from core.telegram_client import SessionAuditGatewayError
from schemas.accounts import AccountCreate, AccountRead
from schemas.session_audit import AccountSession, AuthorizationList
from schemas.session_rekey import StoredRekey
from services.accounts import AccountActionError
from services.accounts import own_authorizations as svc
from services.accounts import session_audit as audit_svc
from services.accounts._session_classify import flag_seller_keys

_IMPORTED = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
_LOGIN = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


def _account() -> AccountRead:
    return AccountRead.model_validate(
        {
            "account_id": "acc-1",
            "status": "alive",
            "created_at": _IMPORTED.isoformat(),
            "updated_at": _IMPORTED.isoformat(),
            "proxy_id": "proxy-1",
            "devices": ["desktop"],
        }
    )


def _session(
    hash_: str,
    *,
    created: datetime | None,
    ours: bool = False,
    current: bool = False,
    app: str = "Telegram Web",
) -> AccountSession:
    return AccountSession(
        hash=hash_,
        is_current=current,
        is_ours=ours,
        official_app=False,
        device_model="Model",
        platform="Windows",
        system_version="10",
        app_name=app,
        ip="192.0.2.10",
        country="Nowhere",
        date_created=created.isoformat() if created else None,
    )


class _Wire:
    def __init__(self) -> None:
        self.sessions = [
            _session("1", created=_IMPORTED - timedelta(days=30), ours=True, current=True),
            _session("2", created=_LOGIN + timedelta(seconds=5)),
            _session("3", created=_LOGIN - timedelta(days=2)),
        ]
        self.failure: Exception | None = None
        self.seen_own: list[dict[str, str]] = []


@pytest_asyncio.fixture
async def wire(monkeypatch: pytest.MonkeyPatch) -> _Wire:
    state = _Wire()
    await create_account(AccountCreate(account_id="acc-1"))

    async def _require(_account_id: str) -> AccountRead:
        return _account()

    async def _read(_a: str, _d: list[str], own: dict[str, str] | None = None) -> AuthorizationList:
        known = dict(own or {})
        state.seen_own.append(known)
        if state.failure is not None:
            raise state.failure
        marked = [
            s.model_copy(update={"is_ours": True, "kind": "web"}) if s.hash in known else s
            for s in state.sessions
        ]
        return AuthorizationList(sessions=marked, ttl_days=180)

    monkeypatch.setattr(audit_svc, "require_account", _require)
    monkeypatch.setattr(audit_svc, "read_account_sessions", _read)
    monkeypatch.setattr(svc, "read_account_sessions", _read)
    return state


class _Window:
    """Weak-referenceable stand-in for a ``WebWindow`` (the baseline is keyed by it)."""


def _window() -> Any:
    return _Window()


async def _record(window: Any, **kw: Any) -> int:
    return await svc.record_own_web_login(
        "acc-1", window, since=kw.get("since", _LOGIN), seen_at=kw.get("seen_at", _LOGIN)
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("wire")
async def test_only_the_authorization_new_since_the_baseline_is_recorded(wire: _Wire) -> None:
    window: Any = _Window()
    wire.sessions = wire.sessions[:1] + wire.sessions[2:]  # "3" listed at launch
    assert await svc.remember_login_baseline("acc-1", window) is True
    wire.sessions.append(_session("2", created=_LOGIN + timedelta(seconds=5)))

    assert await _record(window) == 1

    assert await fetch_own_authorizations("acc-1") == {"2": "web"}


@pytest.mark.asyncio
async def test_a_second_web_login_in_the_window_records_nothing(wire: _Wire) -> None:
    """A seller/thief signing in to Web at the same time must never become ours."""
    window: Any = _Window()
    wire.sessions = wire.sessions[:1]
    await svc.remember_login_baseline("acc-1", window)
    wire.sessions.append(_session("2", created=_LOGIN + timedelta(seconds=5)))
    wire.sessions.append(_session("9", created=_LOGIN + timedelta(seconds=6)))

    assert await _record(window) == 0

    assert await fetch_own_authorizations("acc-1") == {}


@pytest.mark.asyncio
async def test_a_desktop_or_mobile_app_in_the_same_window_is_never_ours(wire: _Wire) -> None:
    window: Any = _Window()
    wire.sessions = wire.sessions[:1]
    await svc.remember_login_baseline("acc-1", window)
    wire.sessions.append(_session("2", created=_LOGIN, app="Telegram Web"))
    wire.sessions.append(_session("4", created=_LOGIN, app="Telegram Desktop"))
    wire.sessions.append(_session("5", created=_LOGIN, app="Telegram iOS"))

    assert await _record(window) == 1

    assert await fetch_own_authorizations("acc-1") == {"2": "web"}


@pytest.mark.asyncio
async def test_without_a_baseline_the_time_window_still_needs_exactly_one(wire: _Wire) -> None:
    window: Any = _Window()  # no baseline taken

    # "2" is inside the window, "3" is two days older: one candidate.
    assert await _record(window) == 1
    assert set(await fetch_own_authorizations("acc-1")) == {"2"}

    wire.sessions.append(_session("7", created=_LOGIN + timedelta(seconds=9)))
    wire.sessions[1] = _session("8", created=_LOGIN + timedelta(seconds=4))
    assert await _record(_Window()) == 0  # "7" and "8" both fit: ambiguous


@pytest.mark.asyncio
async def test_the_time_window_is_bounded_above_by_the_moment_logged_in_was_seen(
    wire: _Wire,
) -> None:
    skew = settings.session_audit.own_login_skew_seconds
    wire.sessions[1] = _session("2", created=_LOGIN + timedelta(seconds=skew + 60))

    assert await _record(_Window()) == 0


@pytest.mark.asyncio
async def test_the_skew_lets_a_slightly_earlier_authorization_in(wire: _Wire) -> None:
    skew = settings.session_audit.own_login_skew_seconds
    wire.sessions = wire.sessions[:1]
    wire.sessions.append(_session("3", created=_LOGIN - timedelta(seconds=skew - 1)))

    assert await _record(_Window()) == 1
    assert set(await fetch_own_authorizations("acc-1")) == {"3"}


@pytest.mark.asyncio
async def test_a_login_that_showed_nothing_new_keeps_its_baseline(wire: _Wire) -> None:
    window: Any = _Window()
    await svc.remember_login_baseline("acc-1", window)

    assert await _record(window) == 0
    wire.sessions.append(_session("9", created=_LOGIN))
    assert await _record(window) == 1  # the next drive of the same window still has it


@pytest.mark.asyncio
async def test_an_unreadable_baseline_is_not_fatal(wire: _Wire) -> None:
    wire.failure = SessionAuditGatewayError("session_dead")

    assert await svc.remember_login_baseline("acc-1", _window()) is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("wire")
async def test_the_saved_snapshot_stops_counting_the_recorded_login_as_foreign(
    wire: _Wire,
) -> None:
    window: Any = _Window()
    wire.sessions = [wire.sessions[0], wire.sessions[2]]
    await svc.remember_login_baseline("acc-1", window)
    wire.sessions.insert(1, _session("2", created=_LOGIN + timedelta(seconds=5)))
    await audit_svc.audit_account_sessions("acc-1")
    before = await fetch_session_audit("acc-1")
    assert before is not None
    assert before.foreign_count == 2

    await _record(window)

    after = await fetch_session_audit("acc-1")
    assert after is not None
    assert after.foreign_count == 1
    assert [(s.hash, s.is_ours, s.kind) for s in after.sessions][1] == ("2", True, "web")
    shown = await audit_svc.read_account_session_audit("acc-1")
    assert shown.foreign_count == 1


@pytest.mark.asyncio
async def test_an_audit_read_forgets_own_rows_that_are_no_longer_listed(wire: _Wire) -> None:
    await save_own_authorizations("acc-1", ["2", "gone"], kind="web", recorded_at="t")

    await audit_svc.audit_account_sessions("acc-1")

    assert await fetch_own_authorizations("acc-1") == {"2": "web"}
    assert wire.sessions  # the listing was not empty


@pytest.mark.asyncio
async def test_an_unreadable_list_is_the_gateways_code(wire: _Wire) -> None:
    wire.failure = SessionAuditGatewayError("session_dead")

    with pytest.raises(AccountActionError) as refused:
        await _record(_Window())

    assert refused.value.code == "session_dead"
    assert await fetch_own_authorizations("acc-1") == {}


def test_a_key_older_than_the_import_is_flagged_as_the_sellers() -> None:
    mine = _session("1", created=_IMPORTED + timedelta(days=1), ours=True)
    sellers = _session("2", created=_IMPORTED - timedelta(days=30), ours=True)
    foreign = _session("3", created=_IMPORTED - timedelta(days=30))

    flagged = flag_seller_keys([mine, sellers, foreign], _IMPORTED.isoformat(), None)

    assert [s.key_predates_import for s in flagged] == [False, True, False]


def test_a_seller_key_older_than_the_rekey_is_still_flagged_after_it() -> None:
    sellers = _session("2", created=_IMPORTED - timedelta(days=30), ours=True)
    rekey = StoredRekey(account_id="acc-1", old_hash="9", rekeyed_at=_LOGIN.isoformat())

    assert flag_seller_keys([sellers], _IMPORTED.isoformat(), rekey)[0].key_predates_import is True


def test_the_key_a_rekey_made_is_not_flagged() -> None:
    made = _session("2", created=_IMPORTED + timedelta(days=2), ours=True)
    rekey = StoredRekey(account_id="acc-1", old_hash="9", rekeyed_at=_LOGIN.isoformat())

    assert flag_seller_keys([made], _IMPORTED.isoformat(), rekey)[0].key_predates_import is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("wire")
async def test_the_audit_saves_the_seller_key_flag() -> None:
    audit = await audit_svc.audit_account_sessions("acc-1")

    assert [s.key_predates_import for s in audit.sessions] == [True, False, False]

    await save_rekey(StoredRekey(account_id="acc-1", old_hash="9", rekeyed_at=_LOGIN.isoformat()))
    again = await audit_svc.audit_account_sessions("acc-1")
    assert [s.key_predates_import for s in again.sessions] == [
        True,
        False,
        False,
    ]  # still older than both
