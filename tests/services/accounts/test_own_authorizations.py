"""Telebuba's own web login is ours: recorded from the listing, never foreign, never closed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from core.config import settings
from core.db import create_account
from core.repositories.account_own_authorizations import fetch_own_authorizations
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
    hash_: str, *, created: datetime | None, ours: bool = False, current: bool = False
) -> AccountSession:
    return AccountSession(
        hash=hash_,
        is_current=current,
        is_ours=ours,
        official_app=False,
        device_model="Model",
        platform="Windows",
        system_version="10",
        app_name="Telegram Desktop",
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


@pytest.mark.asyncio
@pytest.mark.usefixtures("wire")
async def test_only_the_authorization_created_by_the_login_is_recorded() -> None:
    assert await svc.record_own_web_authorizations("acc-1", _LOGIN) == 1

    assert await fetch_own_authorizations("acc-1") == {"2": "web"}


@pytest.mark.asyncio
async def test_the_skew_lets_a_slightly_earlier_authorization_in(wire: _Wire) -> None:
    skew = settings.session_audit.own_authorization_skew_seconds
    wire.sessions[2] = _session("3", created=_LOGIN - timedelta(seconds=skew - 1))

    assert await svc.record_own_web_authorizations("acc-1", _LOGIN) == 2
    assert set(await fetch_own_authorizations("acc-1")) == {"2", "3"}


@pytest.mark.asyncio
async def test_recording_twice_counts_only_what_is_new(wire: _Wire) -> None:
    await svc.record_own_web_authorizations("acc-1", _LOGIN)

    assert await svc.record_own_web_authorizations("acc-1", _LOGIN) == 0
    assert wire.seen_own[-1] == {"2": "web"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("wire")
async def test_the_saved_snapshot_stops_counting_the_recorded_login_as_foreign() -> None:
    await audit_svc.audit_account_sessions("acc-1")
    before = await fetch_session_audit("acc-1")
    assert before is not None
    assert before.foreign_count == 2

    await svc.record_own_web_authorizations("acc-1", _LOGIN)

    after = await fetch_session_audit("acc-1")
    assert after is not None
    assert after.foreign_count == 1
    assert [(s.hash, s.is_ours, s.kind) for s in after.sessions][1] == ("2", True, "web")
    shown = await audit_svc.read_account_session_audit("acc-1")
    assert shown.foreign_count == 1


@pytest.mark.asyncio
async def test_an_unreadable_list_is_the_gateways_code(wire: _Wire) -> None:
    wire.failure = SessionAuditGatewayError("session_dead")

    with pytest.raises(AccountActionError) as refused:
        await svc.record_own_web_authorizations("acc-1", _LOGIN)

    assert refused.value.code == "session_dead"
    assert await fetch_own_authorizations("acc-1") == {}


def test_a_key_older_than_the_import_is_flagged_as_the_sellers() -> None:
    mine = _session("1", created=_IMPORTED + timedelta(days=1), ours=True)
    sellers = _session("2", created=_IMPORTED - timedelta(days=30), ours=True)
    foreign = _session("3", created=_IMPORTED - timedelta(days=30))

    flagged = flag_seller_keys([mine, sellers, foreign], _IMPORTED.isoformat(), None)

    assert [s.key_predates_import for s in flagged] == [False, True, False]


def test_a_rekeyed_account_is_not_flagged() -> None:
    sellers = _session("2", created=_IMPORTED - timedelta(days=30), ours=True)
    rekey = StoredRekey(account_id="acc-1", old_hash="9", rekeyed_at=_LOGIN.isoformat())

    assert flag_seller_keys([sellers], _IMPORTED.isoformat(), rekey)[0].key_predates_import is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("wire")
async def test_the_audit_saves_the_seller_key_flag() -> None:
    audit = await audit_svc.audit_account_sessions("acc-1")

    assert [s.key_predates_import for s in audit.sessions] == [True, False, False]

    await save_rekey(StoredRekey(account_id="acc-1", old_hash="9", rekeyed_at=_LOGIN.isoformat()))
    again = await audit_svc.audit_account_sessions("acc-1")
    assert [s.key_predates_import for s in again.sessions] == [False, False, False]
