"""The session audit gateway: ours told from the rest by asking each of our clients."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon import errors
from telethon.tl.functions.account import GetAuthorizationsRequest

from core.telegram_client import _session_audit
from core.telegram_client._pool import TelegramClientPoolError
from core.telegram_client._session_audit import SessionAuditGatewayError, read_account_sessions

_CREATED = datetime(2026, 10, 31, 8, 30, tzinfo=UTC)


def _auth(hash_: int, *, current: bool = False, **fields: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "hash": hash_,
        "current": current,
        "official_app": False,
        "device_model": "Model",
        "platform": "Windows",
        "system_version": "10",
        "app_name": "Telegram Desktop",
        "ip": "192.0.2.10",
        "country": "Nowhere",
        "date_created": _CREATED,
        "date_active": _CREATED,
        "unconfirmed": False,
    }
    return SimpleNamespace(**(values | fields))


def _reply(*authorizations: SimpleNamespace, ttl: int | None = 180) -> SimpleNamespace:
    return SimpleNamespace(authorization_ttl_days=ttl, authorizations=list(authorizations))


class _Client:
    """Answers ``GetAuthorizationsRequest`` with ``reply`` (or raises it when an exception)."""

    def __init__(self, reply: object) -> None:
        self.reply = reply
        self.requests: list[object] = []

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _borrow(monkeypatch: pytest.MonkeyPatch, clients: dict[str, object]) -> list[str]:
    borrowed: list[str] = []

    async def _get_client(account_id: str, device_type: str | None = None) -> object:
        assert account_id == "acc-1"
        borrowed.append(str(device_type))
        client = clients[str(device_type)]
        if isinstance(client, Exception):
            raise client
        return client

    monkeypatch.setattr(_session_audit, "get_client", _get_client)
    return borrowed


@pytest.mark.asyncio
async def test_one_device_marks_its_own_session_ours_and_the_rest_foreign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(
        _reply(_auth(11, current=True), _auth(22, official_app=True, unconfirmed=True))
    )
    _borrow(monkeypatch, {"desktop": client})

    listing = await read_account_sessions("acc-1", ["desktop"])

    mine, other = listing.sessions
    assert (mine.hash, mine.is_current, mine.is_ours) == ("11", True, True)
    assert (other.hash, other.is_current, other.is_ours) == ("22", False, False)
    assert other.official_app is True
    assert other.unconfirmed is True
    assert other.date_created == _CREATED.isoformat()
    assert listing.ttl_days == 180
    assert [type(request) for request in client.requests] == [GetAuthorizationsRequest]


@pytest.mark.asyncio
async def test_the_second_devices_own_session_is_ours_too(monkeypatch: pytest.MonkeyPatch) -> None:
    # Telegram lists every session from either client; only ``current`` differs.
    desktop = _Client(_reply(_auth(11, current=True), _auth(22), _auth(33)))
    mobile = _Client(_reply(_auth(11), _auth(22, current=True), _auth(33)))
    borrowed = _borrow(monkeypatch, {"desktop": desktop, "mobile": mobile})

    listing = await read_account_sessions("acc-1", ["desktop", "mobile"])

    assert borrowed == ["desktop", "mobile"]
    assert [(s.hash, s.is_current, s.is_ours) for s in listing.sessions] == [
        ("11", True, True),
        ("22", False, True),
        ("33", False, False),
    ]


@pytest.mark.asyncio
async def test_a_look_alike_of_our_device_is_still_foreign(monkeypatch: pytest.MonkeyPatch) -> None:
    # An imported seller session wears the seller's device, so the model decides nothing.
    client = _Client(
        _reply(
            _auth(11, current=True, device_model="Galaxy S23"),
            _auth(22, device_model="Galaxy S23"),
        )
    )
    _borrow(monkeypatch, {"mobile": client})

    listing = await read_account_sessions("acc-1", ["mobile"])

    assert [s.is_ours for s in listing.sessions] == [True, False]


@pytest.mark.asyncio
async def test_an_unreachable_second_device_fails_the_whole_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reading on would list its session as foreign, and a later close would kill it.
    desktop = _Client(_reply(_auth(11, current=True), _auth(22)))
    _borrow(
        monkeypatch, {"desktop": desktop, "mobile": TelegramClientPoolError("acc-1", OSError())}
    )

    with pytest.raises(SessionAuditGatewayError) as raised:
        await read_account_sessions("acc-1", ["desktop", "mobile"])

    assert raised.value.code == "unavailable"


@pytest.mark.asyncio
async def test_a_list_without_our_own_session_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _borrow(monkeypatch, {"desktop": _Client(_reply(_auth(11), _auth(22)))})

    with pytest.raises(SessionAuditGatewayError) as raised:
        await read_account_sessions("acc-1", ["desktop"])

    assert raised.value.code == "session_audit_failed"


@pytest.mark.asyncio
async def test_a_missing_ttl_stays_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _borrow(monkeypatch, {"desktop": _Client(_reply(_auth(11, current=True), ttl=None))})

    assert (await read_account_sessions("acc-1", ["desktop"])).ttl_days is None


@pytest.mark.parametrize(
    ("raised", "code", "seconds"),
    [
        (errors.SessionRevokedError(request=None), "session_dead", None),
        (errors.AuthKeyDuplicatedError(request=None), "session_dead", None),
        (errors.UserDeactivatedBanError(request=None), "account_deactivated", None),
        (errors.FrozenMethodInvalidError(request=None), "account_frozen", None),
        (errors.FloodWaitError(request=None, capture=17), "flood_wait", 17),
        (errors.HashInvalidError(request=None), "session_audit_failed", None),
        (ConnectionError("boom"), "unavailable", None),
        (TimeoutError(), "unavailable", None),
    ],
)
@pytest.mark.asyncio
async def test_every_failure_is_a_bounded_code(
    monkeypatch: pytest.MonkeyPatch, raised: Exception, code: str, seconds: int | None
) -> None:
    _borrow(monkeypatch, {"desktop": _Client(raised)})

    with pytest.raises(SessionAuditGatewayError) as failure:
        await read_account_sessions("acc-1", ["desktop"])

    assert str(failure.value) == code
    assert failure.value.retry_after_seconds == seconds


@pytest.mark.asyncio
async def test_a_pool_refusal_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _borrow(monkeypatch, {"desktop": TelegramClientPoolError("acc-1", OSError("proxy host:1"))})

    with pytest.raises(SessionAuditGatewayError) as failure:
        await read_account_sessions("acc-1", ["desktop"])

    # The class name only: a pool error carries the proxy endpoint.
    assert (failure.value.code, failure.value.error_type) == (
        "unavailable",
        "TelegramClientPoolError",
    )


@pytest.mark.asyncio
async def test_a_recorded_own_authorization_is_ours_with_its_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(_reply(_auth(11, current=True), _auth(22), _auth(33)))
    _borrow(monkeypatch, {"desktop": client})

    listing = await read_account_sessions("acc-1", ["desktop"], {"22": "web"})

    assert [(s.hash, s.is_ours, s.kind) for s in listing.sessions] == [
        ("11", True, None),
        ("22", True, "web"),
        ("33", False, None),
    ]
