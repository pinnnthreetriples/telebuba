"""Telegram lists the asking session's own authorization with ``hash=0``, ``current=True``.

Every other entry carries its real, stable, non-zero hash — the same from every viewer.
A fake that gives the current entry a non-zero hash hides what production shows: a re-key that
stores ``old_hash == "0"`` is then ``not_foreign`` forever, and a second device of ours is
listed as foreign because each client only ever sees ITSELF as ``0`` and the other one by its
real hash.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from telethon.tl.functions.account import GetAuthorizationsRequest

from core.telegram_client import _session_audit, _session_close
from core.telegram_client._rekey import RekeyGatewayError, rekey_session
from core.telegram_client._session_audit import read_account_sessions
from core.telegram_client._session_close import close_sessions

# Importing the fixture under another name keeps ruff's F811 quiet; pytest registers it by
# the attribute name, so tests ask for ``rekey_rig``.
from tests.core.telegram_client.test_rekey import (
    _CREATED,
    _NEW_HASH,
    _USER,
    _Approver,
    _files,
    _New,
    _Rig,
)
from tests.core.telegram_client.test_rekey import rig as rekey_rig  # noqa: F401
from tests.core.telegram_client.test_session_close import _Client, _statuses

# Hashes Telegram shows to everyone, whoever asks.
_APPROVER_HASH = 777  # the imported session, as the NEW session sees it
_FOREIGN_HASH = 555
_DESKTOP_HASH = 111
_MOBILE_HASH = 222
_SELLER_HASH = 333


def _entry(hash_: int, *, current: bool = False) -> SimpleNamespace:
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
        date_created=_CREATED,
        date_active=None,
        unconfirmed=False,
    )


def _me() -> SimpleNamespace:
    """The asking session's own entry, as Telegram really lists it."""
    return _entry(0, current=True)


def _listing(*authorizations: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(authorization_ttl_days=180, authorizations=list(authorizations))


class _ZeroApprover(_Approver):
    """The imported session asking: it sees itself as 0 and the seller's key by real hash."""

    async def __call__(self, request: object) -> object:
        if isinstance(request, GetAuthorizationsRequest) and self.failure is None:
            self.requests.append(request)
            return _listing(_me(), _entry(_FOREIGN_HASH))
        return await super().__call__(request)


class _ZeroNew(_New):
    """The re-keyed client: after sign-in it sees itself as 0, the approver as 777."""

    def __init__(self) -> None:
        super().__init__()
        self.listing = [_me(), _entry(_APPROVER_HASH), _entry(_FOREIGN_HASH)]

    async def __call__(self, request: object) -> object:
        assert isinstance(request, GetAuthorizationsRequest)
        return _listing(*self.listing)


@pytest.mark.asyncio
async def test_rekey_returns_the_real_hash_of_the_imported_authorization_not_zero(
    rekey_rig: _Rig,  # noqa: F811
) -> None:
    rekey_rig.approver = _ZeroApprover()
    rekey_rig.new = _ZeroNew()
    rekey_rig.new.order = rekey_rig.approver.order

    result = await rekey_session("acc-1", "desktop", "acc_rekey", [])

    assert _files(rekey_rig) == {"acc.session": "new"}
    assert result.new_hash == str(_NEW_HASH)
    # 0 is "whoever asks": after the swap it names the NEW session, so ending it is refused.
    assert result.old_hash != "0"
    assert result.old_hash == str(_APPROVER_HASH)
    assert _USER  # the same Telegram user throughout


async def _refused_rekey(rig: _Rig, new_listing: list[SimpleNamespace]) -> RekeyGatewayError:
    rig.approver = _ZeroApprover()
    rig.new = _ZeroNew()
    rig.new.order = rig.approver.order
    rig.new.listing = new_listing
    with pytest.raises(RekeyGatewayError) as raised:
        await rekey_session("acc-1", "desktop", "acc_rekey", [])
    return raised.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "new_listing",
    [
        [_me(), _entry(_FOREIGN_HASH)],  # nothing new: the approver is not listed
        [_me(), _entry(_APPROVER_HASH), _entry(888), _entry(_FOREIGN_HASH)],  # two candidates
    ],
)
async def test_an_old_key_that_cannot_be_named_undoes_the_rekey(
    rekey_rig: _Rig,  # noqa: F811
    new_listing: list[SimpleNamespace],
) -> None:
    error = await _refused_rekey(rekey_rig, new_listing)

    assert error.code == "rekey_old_key_unknown"
    # The imported session stays as it was and the new authorization is ended.
    assert _files(rekey_rig) == {"acc.session": "old"}
    assert "log_out" in rekey_rig.new.events


@pytest.mark.asyncio
async def test_a_stranger_who_logs_in_between_the_two_reads_refuses_the_rekey(
    rekey_rig: _Rig,  # noqa: F811
) -> None:
    # The approver listed [me, 555]; by the time the new client lists, a stranger is there too.
    error = await _refused_rekey(
        rekey_rig, [_me(), _entry(_APPROVER_HASH), _entry(_FOREIGN_HASH), _entry(999)]
    )

    assert error.code == "rekey_old_key_unknown"
    assert _files(rekey_rig) == {"acc.session": "old"}
    assert "log_out" in rekey_rig.new.events


@pytest.mark.asyncio
async def test_an_approver_without_a_creation_date_refuses_the_rekey(
    rekey_rig: _Rig,  # noqa: F811
) -> None:
    class _Undated(_ZeroApprover):
        async def __call__(self, request: object) -> object:
            reply = await super().__call__(request)
            if isinstance(request, GetAuthorizationsRequest):
                reply.authorizations[0].date_created = None  # ty: ignore[unresolved-attribute]
            return reply

    rekey_rig.approver = _Undated()
    rekey_rig.new = _ZeroNew()
    rekey_rig.new.order = rekey_rig.approver.order

    with pytest.raises(RekeyGatewayError) as raised:
        await rekey_session("acc-1", "desktop", "acc_rekey", [])

    assert raised.value.code == "rekey_old_key_unknown"
    assert _files(rekey_rig) == {"acc.session": "old"}


@pytest.mark.asyncio
async def test_the_new_sessions_own_entry_and_hash_are_never_the_old_key(
    rekey_rig: _Rig,  # noqa: F811
) -> None:
    rekey_rig.approver = _ZeroApprover()
    rekey_rig.new = _ZeroNew()
    rekey_rig.new.order = rekey_rig.approver.order
    # Telegram lists the asking entry with a real hash here, and the new hash is listed too.
    rekey_rig.new.listing = [
        _entry(444, current=True),
        _entry(_NEW_HASH),
        _entry(_APPROVER_HASH),
        _entry(_FOREIGN_HASH),
    ]

    result = await rekey_session("acc-1", "desktop", "acc_rekey", [])

    assert result.old_hash == str(_APPROVER_HASH)


@pytest.mark.asyncio
async def test_a_candidate_created_at_another_time_than_the_approver_is_refused(
    rekey_rig: _Rig,  # noqa: F811
) -> None:
    created = datetime(2026, 11, 7, 13, 34, 38, tzinfo=UTC)

    class _Dated(_ZeroApprover):
        async def __call__(self, request: object) -> object:
            reply = await super().__call__(request)
            if isinstance(request, GetAuthorizationsRequest):
                reply.authorizations[0].date_created = created  # ty: ignore[unresolved-attribute]
            return reply

    rekey_rig.approver = _Dated()
    rekey_rig.new = _ZeroNew()
    rekey_rig.new.order = rekey_rig.approver.order
    elsewhere = _entry(_APPROVER_HASH)
    elsewhere.date_created = created + timedelta(hours=1)
    rekey_rig.new.listing = [_me(), elsewhere, _entry(_FOREIGN_HASH)]

    with pytest.raises(RekeyGatewayError) as raised:
        await rekey_session("acc-1", "desktop", "acc_rekey", [])
    assert raised.value.code == "rekey_old_key_unknown"

    # The same entry, created when the approver says it was, is the old key.
    elsewhere.date_created = created
    result = await rekey_session("acc-1", "desktop", "acc_rekey", [])
    assert result.old_hash == str(_APPROVER_HASH)


def _borrow_primary(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    async def _get_client(_account_id: str, _device: str | None = None) -> _Client:
        return client

    monkeypatch.setattr(_session_audit, "get_client", _get_client)
    monkeypatch.setattr(_session_close, "get_client", _get_client)


@pytest.mark.asyncio
async def test_closing_the_zero_hash_is_a_dead_end_but_the_real_old_hash_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # After the swap the NEW primary lists itself as 0 and the old imported key as 777.
    client = _Client([_me(), _entry(_APPROVER_HASH)])
    _borrow_primary(monkeypatch, client)

    # What today's re-key stores: 0 is "ours" forever, so the old key is never ended.
    dead_end = await close_sessions("acc-1", ["desktop"], ["0"], [])
    assert _statuses(dead_end) == [("0", "not_foreign")]
    assert client.reset_hashes == []

    # The target: the real hash of the old authorization is foreign and gets ended.
    outcomes = await close_sessions("acc-1", ["desktop"], [str(_APPROVER_HASH)], [])
    assert _statuses(outcomes) == [(str(_APPROVER_HASH), "closed")]
    assert client.reset_hashes == [_APPROVER_HASH]


@pytest.mark.asyncio
async def test_a_second_own_device_is_ours_when_each_client_sees_itself_as_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = _Client([_me(), _entry(_MOBILE_HASH), _entry(_SELLER_HASH)])
    mobile = _Client([_me(), _entry(_DESKTOP_HASH), _entry(_SELLER_HASH)])
    clients = {"desktop": desktop, "mobile": mobile}

    async def _get_client(_account_id: str, device: str | None = None) -> _Client:
        return clients[str(device)]

    monkeypatch.setattr(_session_audit, "get_client", _get_client)

    listing = await read_account_sessions("acc-1", ["desktop", "mobile"])

    ours = {s.hash: s.is_ours for s in listing.sessions if s.hash != "0"}
    assert ours[str(_MOBILE_HASH)] is True  # our own second device
    assert ours[str(_SELLER_HASH)] is False  # somebody else's
    assert [s.is_ours for s in listing.sessions if s.hash == "0"] == [True]  # the asking one


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "other_entries",
    [
        # The second device lists everything the primary does: it is not told apart.
        [_entry(_MOBILE_HASH), _entry(_SELLER_HASH)],
        # It lists nothing of the primary's: two candidates, so none can be named.
        [_entry(_DESKTOP_HASH)],
    ],
)
async def test_a_second_device_that_cannot_be_identified_fails_the_whole_read(
    monkeypatch: pytest.MonkeyPatch, other_entries: list[SimpleNamespace]
) -> None:
    desktop = _Client([_me(), _entry(_MOBILE_HASH), _entry(_SELLER_HASH)])
    mobile = _Client([_me(), *other_entries])
    clients = {"desktop": desktop, "mobile": mobile}

    async def _get_client(_account_id: str, device: str | None = None) -> _Client:
        return clients[str(device)]

    monkeypatch.setattr(_session_audit, "get_client", _get_client)

    with pytest.raises(_session_audit.SessionAuditGatewayError) as raised:
        await read_account_sessions("acc-1", ["desktop", "mobile"])
    assert str(raised.value) == "session_audit_failed"
