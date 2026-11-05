"""Closing foreign sessions: only named, still-foreign hashes, one call each, never ours."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from telethon import errors
from telethon.tl.functions.account import GetAuthorizationsRequest, ResetAuthorizationRequest

from core.telegram_client import _session_audit, _session_close
from core.telegram_client._session_audit import SessionAuditGatewayError
from core.telegram_client._session_close import close_sessions


def _auth(hash_: int, *, current: bool = False, created: datetime | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        hash=hash_,
        current=current,
        official_app=False,
        device_model="Model",
        platform="Windows",
        system_version="10",
        app_name="Telegram Desktop",
        ip="192.0.2.10",
        country="Nowhere",
        date_created=created,
        date_active=created,
        unconfirmed=False,
    )


class _Client:
    """Lists ``authorizations``; each reset answers from ``resets`` by hash (default: ok)."""

    def __init__(
        self, authorizations: list[SimpleNamespace], resets: dict[int, object] | None = None
    ) -> None:
        self.authorizations = authorizations
        self.resets = resets or {}
        self.reset_hashes: list[int] = []

    async def __call__(self, request: object) -> object:
        if isinstance(request, GetAuthorizationsRequest):
            return SimpleNamespace(authorization_ttl_days=180, authorizations=self.authorizations)
        assert isinstance(request, ResetAuthorizationRequest)
        self.reset_hashes.append(request.hash)
        answer = self.resets.get(request.hash, True)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture(autouse=True)
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    pauses: list[float] = []

    async def _sleep(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr(_session_close.asyncio, "sleep", _sleep)
    return pauses


def _borrow(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    async def _get_client(_account_id: str, _device: str | None = None) -> _Client:
        return client

    monkeypatch.setattr(_session_audit, "get_client", _get_client)
    monkeypatch.setattr(_session_close, "get_client", _get_client)


def _statuses(outcomes: list) -> list[tuple[str, str]]:
    return [(outcome.hash, outcome.status) for outcome in outcomes]


@pytest.mark.asyncio
async def test_each_named_foreign_session_is_closed_with_a_drawn_pause_between(
    monkeypatch: pytest.MonkeyPatch, slept: list[float]
) -> None:
    client = _Client([_auth(1, current=True), _auth(2), _auth(3), _auth(4)])
    _borrow(monkeypatch, client)

    outcomes = await close_sessions("acc-1", ["desktop"], ["2", "3", "4"], [11.0, 22.0])

    assert _statuses(outcomes) == [("2", "closed"), ("3", "closed"), ("4", "closed")]
    assert client.reset_hashes == [2, 3, 4]
    assert slept == [11.0, 22.0]


@pytest.mark.asyncio
async def test_one_of_our_own_sessions_is_never_closed_whatever_is_asked(
    monkeypatch: pytest.MonkeyPatch, slept: list[float]
) -> None:
    client = _Client([_auth(1, current=True), _auth(2)])
    _borrow(monkeypatch, client)

    outcomes = await close_sessions("acc-1", ["desktop"], ["1", "2"], [5.0])

    assert _statuses(outcomes) == [("1", "not_foreign"), ("2", "closed")]
    assert client.reset_hashes == [2]
    assert slept == []


@pytest.mark.asyncio
async def test_the_second_devices_session_is_ours_and_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = _Client([_auth(1, current=True), _auth(2), _auth(3)])
    mobile = _Client([_auth(1), _auth(2, current=True), _auth(3)])
    clients = {"desktop": desktop, "mobile": mobile}

    async def _get_client(_account_id: str, device: str | None = None) -> _Client:
        return clients[str(device)]

    monkeypatch.setattr(_session_audit, "get_client", _get_client)
    monkeypatch.setattr(_session_close, "get_client", _get_client)

    outcomes = await close_sessions("acc-1", ["desktop", "mobile"], ["2", "3"], [1.0])

    assert _statuses(outcomes) == [("2", "not_foreign"), ("3", "closed")]
    assert desktop.reset_hashes == [3]
    assert mobile.reset_hashes == []


@pytest.mark.asyncio
async def test_a_hash_no_longer_listed_is_already_done(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client([_auth(1, current=True)])
    _borrow(monkeypatch, client)

    outcomes = await close_sessions("acc-1", ["desktop"], ["9"], [])

    assert _statuses(outcomes) == [("9", "gone")]
    assert client.reset_hashes == []


@pytest.mark.asyncio
async def test_a_hash_telegram_no_longer_knows_counts_as_done_and_the_run_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(
        [_auth(1, current=True), _auth(2), _auth(3)],
        {2: errors.HashInvalidError(request=None)},
    )
    _borrow(monkeypatch, client)

    outcomes = await close_sessions("acc-1", ["desktop"], ["2", "3"], [1.0])

    assert _statuses(outcomes) == [("2", "gone"), ("3", "closed")]


@pytest.mark.asyncio
async def test_a_session_too_young_to_close_others_ends_the_run_with_the_time_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = datetime.now(UTC) - timedelta(hours=20)
    client = _Client(
        [_auth(1, current=True, created=created), _auth(2), _auth(3)],
        {2: errors.FreshResetAuthorisationForbiddenError(request=None)},
    )
    _borrow(monkeypatch, client)

    outcomes = await close_sessions("acc-1", ["desktop"], ["2", "3"], [1.0])

    assert _statuses(outcomes) == [("2", "too_new"), ("3", "skipped")]
    # Four hours short of a day, to the minute.
    assert outcomes[0].retry_after_seconds is not None
    assert abs(outcomes[0].retry_after_seconds - 4 * 3600) < 120
    assert client.reset_hashes == [2]


@pytest.mark.asyncio
async def test_too_young_without_a_known_age_carries_no_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(
        [_auth(1, current=True), _auth(2)],
        {2: errors.FreshResetAuthorisationForbiddenError(request=None)},
    )
    _borrow(monkeypatch, client)

    outcomes = await close_sessions("acc-1", ["desktop"], ["2"], [])

    assert (outcomes[0].status, outcomes[0].retry_after_seconds) == ("too_new", None)


@pytest.mark.asyncio
async def test_a_flood_wait_ends_the_run_with_its_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client(
        [_auth(1, current=True), _auth(2), _auth(3)],
        {2: errors.FloodWaitError(request=None, capture=33)},
    )
    _borrow(monkeypatch, client)

    outcomes = await close_sessions("acc-1", ["desktop"], ["2", "3"], [1.0])

    assert _statuses(outcomes) == [("2", "flood_wait"), ("3", "skipped")]
    assert outcomes[0].retry_after_seconds == 33


@pytest.mark.asyncio
async def test_any_other_refusal_ends_the_run_keeping_what_was_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(
        [_auth(1, current=True), _auth(2), _auth(3), _auth(4)],
        {3: errors.RPCError(request=None, message="BOOM", code=400)},
    )
    _borrow(monkeypatch, client)

    outcomes = await close_sessions("acc-1", ["desktop"], ["2", "3", "4"], [1.0, 1.0])

    assert _statuses(outcomes) == [("2", "closed"), ("3", "failed"), ("4", "skipped")]
    assert outcomes[1].error_type == "RPCError"


@pytest.mark.asyncio
async def test_a_dead_session_before_the_first_call_is_a_gateway_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Dead(_Client):
        async def __call__(self, request: object) -> object:
            raise errors.SessionRevokedError(request=request)

    _borrow(monkeypatch, _Dead([]))

    with pytest.raises(SessionAuditGatewayError) as raised:
        await close_sessions("acc-1", ["desktop"], ["2"], [])

    assert raised.value.code == "session_dead"


@pytest.mark.asyncio
async def test_a_recorded_own_web_login_is_never_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client([_auth(1, current=True), _auth(2), _auth(3)])
    _borrow(monkeypatch, client)

    outcomes = await close_sessions("acc-1", ["desktop"], ["2", "3"], [1.0], {"2": "web"})

    assert _statuses(outcomes) == [("2", "not_foreign"), ("3", "closed")]
    assert client.reset_hashes == [3]
