"""Session audit policy: refuse without a proxy, save what Telegram lists, journal news once."""

from __future__ import annotations

import pytest

from core.repositories.account_rekeys import fetch_rekey, save_rekey
from core.repositories.lifecycle import list_lifecycle_events
from core.repositories.session_audits import fetch_last_session_audit_event, fetch_session_audit
from core.telegram_client import SessionAuditGatewayError
from schemas.accounts import AccountRead
from schemas.session_audit import AccountSession, AuthorizationList
from schemas.session_rekey import StoredRekey
from services.accounts import AccountActionError, AccountNotFoundError
from services.accounts import session_audit as svc


def _account(**fields: object) -> AccountRead:
    values: dict[str, object] = {
        "account_id": "acc-1",
        "status": "alive",
        "created_at": "2026-10-31T00:00:00+00:00",
        "updated_at": "2026-10-31T00:00:00+00:00",
        "proxy_id": "proxy-1",
        "devices": ["desktop"],
    }
    return AccountRead.model_validate(values | fields)


def _session(hash_: str, *, ours: bool) -> AccountSession:
    return AccountSession(
        hash=hash_,
        is_current=hash_ == "1",
        is_ours=ours,
        official_app=False,
        device_model="Model",
        platform="Windows",
        system_version="10",
        app_name="Telegram Desktop",
        ip="192.0.2.10",
        country="Nowhere",
    )


class _Wire:
    """The two seams the service crosses: the account row and Telegram's session list."""

    def __init__(self) -> None:
        self.account: AccountRead | None = _account()
        self.listing = AuthorizationList(sessions=[_session("1", ours=True)], ttl_days=180)
        self.failure: Exception | None = None
        self.reads: list[tuple[str, list[str]]] = []


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> _Wire:
    state = _Wire()

    async def _require(account_id: str) -> AccountRead:
        if state.account is None:
            raise AccountNotFoundError(account_id)
        return state.account

    async def _read(account_id: str, devices: list[str], _own: object = None) -> AuthorizationList:
        state.reads.append((account_id, list(devices)))
        if state.failure is not None:
            raise state.failure
        return state.listing

    monkeypatch.setattr(svc, "require_account", _require)
    monkeypatch.setattr(svc, "read_account_sessions", _read)
    return state


async def _journal(kind: str = "foreign_sessions_found") -> list[dict[str, object]]:
    return [e.extra for e in await list_lifecycle_events("acc-1") if e.kind == kind]


@pytest.mark.asyncio
async def test_an_account_without_a_proxy_is_refused_before_telegram(wire: _Wire) -> None:
    wire.account = _account(proxy_id=None)

    with pytest.raises(AccountActionError) as refused:
        await svc.audit_account_sessions("acc-1")

    assert refused.value.code == "session_audit_proxy_missing"
    assert wire.reads == []


@pytest.mark.asyncio
async def test_an_unknown_account_is_not_found(wire: _Wire) -> None:
    wire.account = None

    with pytest.raises(AccountNotFoundError):
        await svc.audit_account_sessions("acc-1")
    with pytest.raises(AccountNotFoundError):
        await svc.read_account_session_audit("acc-1")


@pytest.mark.asyncio
async def test_the_audit_reads_every_device_saves_and_keeps_history(wire: _Wire) -> None:
    wire.account = _account(devices=["desktop", "mobile"])

    audit = await svc.audit_account_sessions("acc-1")

    assert wire.reads == [("acc-1", ["desktop", "mobile"])]
    assert (audit.total, audit.foreign_count, audit.ttl_days) == (1, 0, 180)
    assert await fetch_session_audit("acc-1") == audit
    event = await fetch_last_session_audit_event("acc-1")
    assert event is not None
    assert (event.total, event.foreign_count, event.killed_count) == (1, 0, 0)
    assert await _journal() == []


@pytest.mark.asyncio
async def test_a_new_foreign_session_is_journalled_with_counts_only(wire: _Wire) -> None:
    wire.listing = AuthorizationList(sessions=[_session("1", ours=True), _session("2", ours=False)])

    audit = await svc.audit_account_sessions("acc-1")

    assert audit.foreign_count == 1
    assert await _journal() == [{"total": 2, "foreign": 1, "new": 1}]


@pytest.mark.asyncio
async def test_a_session_already_known_is_not_journalled_again(wire: _Wire) -> None:
    wire.listing = AuthorizationList(sessions=[_session("1", ours=True), _session("2", ours=False)])
    await svc.audit_account_sessions("acc-1")

    await svc.audit_account_sessions("acc-1")
    assert len(await _journal()) == 1

    wire.listing = AuthorizationList(
        sessions=[_session("1", ours=True), _session("2", ours=False), _session("3", ours=False)]
    )
    await svc.audit_account_sessions("acc-1")
    assert await _journal() == [
        {"total": 2, "foreign": 1, "new": 1},
        {"total": 3, "foreign": 2, "new": 1},
    ]


@pytest.mark.asyncio
async def test_a_failed_read_surfaces_its_code_and_saves_nothing(wire: _Wire) -> None:
    wire.failure = SessionAuditGatewayError("flood_wait", retry_after_seconds=30)

    with pytest.raises(AccountActionError) as refused:
        await svc.audit_account_sessions("acc-1")

    assert (refused.value.code, refused.value.retry_after_seconds) == ("flood_wait", 30)
    assert await fetch_session_audit("acc-1") is None


@pytest.mark.asyncio
async def test_a_dead_session_is_reported_as_one(wire: _Wire) -> None:
    wire.failure = SessionAuditGatewayError("session_dead", error_type="SessionRevokedError")

    with pytest.raises(AccountActionError) as refused:
        await svc.audit_account_sessions("acc-1")

    assert refused.value.code == "session_dead"


@pytest.mark.asyncio
async def test_the_view_is_empty_before_the_first_audit(wire: _Wire) -> None:
    view = await svc.read_account_session_audit("acc-1")

    assert (view.account_id, view.checked_at, view.total, view.sessions) == ("acc-1", None, 0, [])
    assert wire.reads == []


@pytest.mark.asyncio
async def test_the_view_serves_the_saved_audit_without_calling_telegram(wire: _Wire) -> None:
    saved = await svc.audit_account_sessions("acc-1")
    wire.reads.clear()

    assert await svc.read_account_session_audit("acc-1") == saved
    assert wire.reads == []


@pytest.mark.asyncio
async def test_the_first_check_audits_once(wire: _Wire) -> None:
    account = _account()

    await svc.audit_after_first_check(account)
    await svc.audit_after_first_check(account)

    assert len(wire.reads) == 1
    assert await fetch_session_audit("acc-1") is not None


@pytest.mark.asyncio
async def test_the_first_check_stays_offline_without_a_proxy(wire: _Wire) -> None:
    await svc.audit_after_first_check(_account(proxy_id=None))

    assert wire.reads == []
    assert await fetch_session_audit("acc-1") is None


@pytest.mark.asyncio
async def test_a_refused_first_audit_never_breaks_the_check(wire: _Wire) -> None:
    wire.failure = SessionAuditGatewayError("unavailable")

    await svc.audit_after_first_check(_account())

    assert await fetch_session_audit("acc-1") is None


def _stuck(old_hash: str, problem: str = "old_key_is_ours") -> StoredRekey:
    return StoredRekey(
        account_id="acc-1",
        old_hash=old_hash,
        rekeyed_at="2026-11-07T13:34:45+00:00",
        problem=problem,  # ty: ignore[invalid-argument-type]
    )


@pytest.mark.asyncio
async def test_an_audit_that_no_longer_lists_the_old_key_clears_the_problem(wire: _Wire) -> None:
    await save_rekey(_stuck("77"))
    wire.listing = AuthorizationList(sessions=[_session("1", ours=True)], ttl_days=180)

    await svc.audit_account_sessions("acc-1")

    rekey = await fetch_rekey("acc-1")
    assert rekey is not None
    assert (rekey.problem, rekey.closed_at is not None) == (None, True)


@pytest.mark.asyncio
async def test_a_problem_stays_while_its_hash_is_still_listed(wire: _Wire) -> None:
    await save_rekey(_stuck("77"))
    wire.listing = AuthorizationList(
        sessions=[_session("1", ours=True), _session("77", ours=False)], ttl_days=180
    )

    await svc.audit_account_sessions("acc-1")

    rekey = await fetch_rekey("acc-1")
    assert rekey is not None
    assert (rekey.problem, rekey.closed_at) == ("old_key_is_ours", None)


@pytest.mark.asyncio
@pytest.mark.usefixtures("wire")
async def test_a_zero_hash_problem_is_never_cleared_by_an_audit() -> None:
    await save_rekey(_stuck("0", "old_key_unknown"))

    await svc.audit_account_sessions("acc-1")

    rekey = await fetch_rekey("acc-1")
    assert rekey is not None
    assert (rekey.problem, rekey.closed_at) == ("old_key_unknown", None)
