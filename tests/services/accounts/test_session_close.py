"""Closing foreign sessions: proxy first, drawn pauses, a fresh audit after, counts journalled."""

from __future__ import annotations

import pytest

from core.repositories.lifecycle import list_lifecycle_events
from core.repositories.session_audits import fetch_last_session_audit_event, fetch_session_audit
from core.telegram_client import SessionAuditGatewayError
from schemas.accounts import AccountRead
from schemas.session_audit import AccountSession, AuthorizationList
from schemas.session_close import SessionCloseOutcome
from services.accounts import AccountActionError, AccountNotFoundError
from services.accounts import session_audit as audit_svc
from services.accounts import session_close as svc


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


def _listing(*foreign: str) -> AuthorizationList:
    return AuthorizationList(
        sessions=[_session("1", ours=True), *(_session(h, ours=False) for h in foreign)]
    )


class _Wire:
    def __init__(self) -> None:
        self.account: AccountRead | None = _account()
        # What Telegram lists before the run and after it.
        self.listings = [_listing("2", "3"), _listing()]
        self.reads = 0
        self.closes: list[tuple[list[str], int]] = []
        self.statuses: dict[str, str] = {}
        self.failure: Exception | None = None


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> _Wire:
    state = _Wire()

    async def _require(account_id: str) -> AccountRead:
        if state.account is None:
            raise AccountNotFoundError(account_id)
        return state.account

    async def _read(_account_id: str, _devices: list[str]) -> AuthorizationList:
        listing = state.listings[min(state.reads, len(state.listings) - 1)]
        state.reads += 1
        return listing

    async def _close(
        _account_id: str, _devices: list[str], hashes: list[str], pauses: list[float]
    ) -> list[SessionCloseOutcome]:
        if state.failure is not None:
            raise state.failure
        state.closes.append((list(hashes), len(pauses)))
        return [
            SessionCloseOutcome(hash=h, status=state.statuses.get(h, "closed"))  # ty: ignore[invalid-argument-type]
            for h in hashes
        ]

    monkeypatch.setattr(audit_svc, "require_account", _require)
    monkeypatch.setattr(audit_svc, "read_account_sessions", _read)
    monkeypatch.setattr(svc, "close_sessions", _close)
    return state


async def _closed_rows() -> list[dict[str, object]]:
    events = await list_lifecycle_events("acc-1")
    return [e.extra for e in events if e.kind == "foreign_sessions_closed"]


@pytest.mark.asyncio
async def test_an_account_without_a_proxy_is_refused_before_telegram(wire: _Wire) -> None:
    wire.account = _account(proxy_id=None)

    with pytest.raises(AccountActionError) as refused:
        await svc.close_account_sessions("acc-1", ["2"])

    assert refused.value.code == "session_audit_proxy_missing"
    assert (wire.reads, wire.closes) == (0, [])


@pytest.mark.asyncio
async def test_an_unknown_account_is_not_found(wire: _Wire) -> None:
    wire.account = None

    with pytest.raises(AccountNotFoundError):
        await svc.close_account_foreign_sessions("acc-1")


@pytest.mark.asyncio
async def test_named_sessions_are_closed_audited_again_and_kept_in_the_history(
    wire: _Wire,
) -> None:
    wire.listings = [_listing()]  # the one read: after the run

    result = await svc.close_account_sessions("acc-1", ["2", "3", "2"])

    # Duplicates collapse, one pause for every gap between sessions.
    assert wire.closes == [(["2", "3"], 1)]
    assert [(o.hash, o.status) for o in result.outcomes] == [("2", "closed"), ("3", "closed")]
    assert result.closed_count == 2
    assert result.audit is not None
    assert (result.audit.total, result.audit.foreign_count) == (1, 0)
    assert await fetch_session_audit("acc-1") == result.audit
    event = await fetch_last_session_audit_event("acc-1")
    assert event is not None
    assert (event.foreign_count, event.killed_count) == (0, 2)
    assert await _closed_rows() == [{"requested": 2, "closed": 2}]


@pytest.mark.asyncio
async def test_the_pauses_are_drawn_within_the_configured_range(
    wire: _Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawn: list[float] = []

    async def _close(
        _a: str, _d: list[str], hashes: list[str], pauses: list[float]
    ) -> list[SessionCloseOutcome]:
        drawn.extend(pauses)
        return [SessionCloseOutcome(hash=h, status="closed") for h in hashes]

    monkeypatch.setattr(svc, "close_sessions", _close)
    monkeypatch.setattr(svc.settings.session_audit, "close_pause_min_seconds", 20.0)
    monkeypatch.setattr(svc.settings.session_audit, "close_pause_max_seconds", 60.0)
    wire.listings = [_listing("2", "3", "4", "5")]

    await svc.close_account_sessions("acc-1", ["2", "3", "4", "5"])

    assert len(drawn) == 3
    assert all(20.0 <= pause <= 60.0 for pause in drawn)


@pytest.mark.asyncio
async def test_a_refusal_is_an_outcome_not_an_error_and_is_not_journalled_as_closed(
    wire: _Wire,
) -> None:
    wire.statuses = {"2": "too_new", "3": "skipped"}
    wire.listings = [_listing("2", "3")]

    result = await svc.close_account_sessions("acc-1", ["2", "3"])

    assert [o.status for o in result.outcomes] == ["too_new", "skipped"]
    assert result.closed_count == 0
    assert await _closed_rows() == []


@pytest.mark.asyncio
async def test_closing_everything_foreign_reads_first_and_names_only_foreign_hashes(
    wire: _Wire,
) -> None:
    result = await svc.close_account_foreign_sessions("acc-1")

    assert wire.closes == [(["2", "3"], 1)]
    assert result.closed_count == 2


@pytest.mark.asyncio
async def test_with_nothing_foreign_nothing_is_closed_and_nothing_is_journalled(
    wire: _Wire,
) -> None:
    wire.listings = [_listing()]

    result = await svc.close_account_foreign_sessions("acc-1")

    assert (wire.closes, result.outcomes, result.closed_count) == ([], [], 0)
    assert result.audit is not None
    assert await _closed_rows() == []


@pytest.mark.asyncio
async def test_a_gateway_refusal_before_the_first_call_is_its_code(wire: _Wire) -> None:
    wire.failure = SessionAuditGatewayError("flood_wait", retry_after_seconds=40)

    with pytest.raises(AccountActionError) as refused:
        await svc.close_account_sessions("acc-1", ["2"])

    assert (refused.value.code, refused.value.retry_after_seconds) == ("flood_wait", 40)
    assert await _closed_rows() == []


@pytest.mark.asyncio
async def test_a_failed_reread_still_reports_what_was_closed(
    wire: _Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _read(_a: str, _d: list[str]) -> AuthorizationList:
        wire.reads += 1
        if wire.reads > 1:
            code = "unavailable"
            raise SessionAuditGatewayError(code)
        return _listing("2")

    monkeypatch.setattr(audit_svc, "read_account_sessions", _read)

    result = await svc.close_account_foreign_sessions("acc-1")

    assert result.closed_count == 1
    assert result.audit is None
    assert await _closed_rows() == [{"requested": 1, "closed": 1}]
