"""Re-keying a session: a login of its own, swapped in only once it is proven, else untouched."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon import errors
from telethon.tl.functions.account import (
    ChangeAuthorizationSettingsRequest,
    GetAuthorizationsRequest,
)
from telethon.tl.functions.auth import AcceptLoginTokenRequest

from core.telegram_client import _rekey
from core.telegram_client._rekey import RekeyGatewayError, rekey_session

_USER = 4242
_OLD_HASH = 111
_NEW_HASH = 222
# When the imported session was created: Telegram always says, and the re-key needs it.
_CREATED = datetime(2026, 11, 7, 13, 34, 38, tzinfo=UTC)


def _write(path: Path, marker: str) -> None:
    """A real SQLite file, as Telethon's session is, holding ``marker``."""
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("create table if not exists marker (v text)")
        db.execute("delete from marker")
        db.execute("insert into marker values (?)", (marker,))


def _read(path: Path) -> str:
    with closing(sqlite3.connect(path)) as db:
        return str(db.execute("select v from marker").fetchone()[0])


class _Approver:
    """The imported session: lists its own authorization, accepts the token."""

    def __init__(self, *, user_id: int | None = _USER, unconfirmed: bool = False) -> None:
        self.user_id = user_id
        self.unconfirmed = unconfirmed
        self.requests: list[object] = []
        self.order: list[str] = []
        self.failure: Exception | None = None
        self.created: object = _CREATED

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        if isinstance(request, GetAuthorizationsRequest):
            # Telegram lists the asking session as hash 0, current.
            return SimpleNamespace(
                authorization_ttl_days=180,
                authorizations=[SimpleNamespace(hash=0, current=True, date_created=self.created)],
            )
        if isinstance(request, AcceptLoginTokenRequest):
            self.order.append("accept")
            return SimpleNamespace(hash=_NEW_HASH, unconfirmed=self.unconfirmed)
        return True

    async def get_me(self) -> object:
        return None if self.user_id is None else SimpleNamespace(id=self.user_id)


class _New:
    """The fresh client: signs in by token, then answers as ``user_id``."""

    def __init__(self, *, user_id: int = _USER) -> None:
        self.user_id = user_id
        self.needs_password = False
        self.password_signed_in_with: str | None = None
        self.wait_failure: Exception | None = None
        self.events: list[str] = []
        self.order: list[str] = []
        self.connected = True
        # What the new session lists once signed in: itself as 0, the imported one by hash.
        self.listing: list[SimpleNamespace] = [
            SimpleNamespace(hash=0, current=True, date_created=None),
            SimpleNamespace(hash=_OLD_HASH, current=False, date_created=_CREATED),
        ]

    async def __call__(self, request: object) -> object:
        assert isinstance(request, GetAuthorizationsRequest)
        return SimpleNamespace(authorization_ttl_days=180, authorizations=self.listing)

    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> None:
        self.connected = True
        self.events.append("connect")

    async def disconnect(self) -> None:
        self.connected = False
        self.events.append("disconnect")

    async def log_out(self) -> None:
        self.events.append("log_out")

    async def get_me(self) -> object:
        return SimpleNamespace(id=self.user_id)

    async def qr_login(self) -> object:
        async def wait(timeout: float) -> None:  # noqa: ASYNC109 - mirrors Telethon's QRLogin.wait
            assert timeout > 0
            self.order.append("wait")
            if self.wait_failure is not None:
                raise self.wait_failure
            if self.needs_password:
                raise errors.SessionPasswordNeededError(request=None)

        return SimpleNamespace(token=b"token", wait=wait)

    async def sign_in(self, *, password: str) -> None:
        self.password_signed_in_with = password
        self.events.append("sign_in")


class _Check:
    """The client the swap opens on the final file to prove it works."""

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def get_me(self) -> object:
        return SimpleNamespace(id=self.user_id)


class _Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.dir = tmp_path
        self.final = tmp_path / "acc.session"
        self.scratch = tmp_path / "acc_rekey.session"
        self.aside = tmp_path / "acc.session.rekey-aside"
        _write(self.final, "old")
        self.approver = _Approver()
        self.new = _New()
        self.new.order = self.approver.order
        self.check_user = _USER


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Rig:
    state = _Rig(tmp_path)

    async def _get_client(_account_id: str, _device: str | None = None) -> _Approver:
        return state.approver

    async def _prepare(request: object, **_: object) -> object:
        name = getattr(request, "session_name", None)
        path = state.scratch if name else state.final
        return SimpleNamespace(
            account_id="acc-1", session_path=str(path.with_suffix("")), device_type="desktop"
        )

    def _create(profile: object) -> object:
        path = Path(f"{getattr(profile, 'session_path', '')}.session")
        if path == state.scratch:
            _write(path, "new")  # Telethon makes the file when the client is built
            return state.new
        return _Check(state.check_user)

    monkeypatch.setattr(_rekey, "get_client", _get_client)
    monkeypatch.setattr(_rekey, "prepare_telegram_client_profile", _prepare)
    monkeypatch.setattr(_rekey, "create_telegram_client", _create)
    return state


async def _run(*passwords: str) -> object:
    return await rekey_session("acc-1", "desktop", "acc_rekey", list(passwords))


def _files(rig: _Rig) -> dict[str, str]:
    return {p.name: _read(p) for p in rig.dir.iterdir() if ".session" in p.name}


@pytest.mark.asyncio
async def test_the_new_session_takes_the_files_place_and_the_old_hash_is_returned(
    rig: _Rig,
) -> None:
    result = await _run()

    assert (result.old_hash, result.new_hash) == (str(_OLD_HASH), str(_NEW_HASH))  # ty: ignore[unresolved-attribute]
    # The imported file is gone, the scratch name is free, the new key sits in its place.
    assert _files(rig) == {"acc.session": "new"}
    accepted = [r for r in rig.approver.requests if isinstance(r, AcceptLoginTokenRequest)]
    assert [r.token for r in accepted] == [b"token"]
    assert not any(isinstance(r, ChangeAuthorizationSettingsRequest) for r in rig.approver.requests)


@pytest.mark.asyncio
async def test_the_new_session_listens_before_the_token_is_accepted(rig: _Rig) -> None:
    # Telegram answers the acceptance with an update; one sent before anything listens
    # is lost and the login times out with the token already accepted.
    await _run()

    assert rig.approver.order == ["wait", "accept"]


@pytest.mark.asyncio
async def test_a_login_flagged_unconfirmed_is_confirmed_by_the_approving_session(rig: _Rig) -> None:
    rig.approver.unconfirmed = True

    await _run()

    confirms = [
        r for r in rig.approver.requests if isinstance(r, ChangeAuthorizationSettingsRequest)
    ]
    assert [(r.hash, r.confirmed) for r in confirms] == [(_NEW_HASH, True)]


@pytest.mark.asyncio
async def test_a_cloud_password_finishes_the_login(rig: _Rig) -> None:
    rig.new.needs_password = True

    await _run("hunter2-hunter2")

    assert rig.new.password_signed_in_with == "hunter2-hunter2"
    assert _files(rig) == {"acc.session": "new"}


@pytest.mark.asyncio
async def test_a_rejected_candidate_moves_on_to_the_next(rig: _Rig) -> None:
    rig.new.needs_password = True
    tried: list[str] = []

    async def _sign_in(*, password: str) -> None:
        tried.append(password)
        if password == "old-password":
            raise errors.PasswordHashInvalidError(request=None)

    rig.new.sign_in = _sign_in  # ty: ignore[invalid-assignment]

    await _run("old-password", "new-password")

    assert tried == ["old-password", "new-password"]
    assert _files(rig) == {"acc.session": "new"}


@pytest.mark.asyncio
async def test_a_cloud_password_that_is_not_stored_changes_nothing(rig: _Rig) -> None:
    rig.new.needs_password = True

    with pytest.raises(RekeyGatewayError) as refused:
        await _run()

    assert refused.value.code == "rekey_twofa_missing"
    assert _files(rig) == {"acc.session": "old"}


@pytest.mark.asyncio
async def test_a_refused_cloud_password_changes_nothing(rig: _Rig) -> None:
    rig.new.needs_password = True

    async def _refuse(*, password: str) -> None:  # noqa: ARG001 - mirrors Telethon's sign_in
        raise errors.PasswordHashInvalidError(request=None)

    rig.new.sign_in = _refuse  # ty: ignore[invalid-assignment]

    with pytest.raises(RekeyGatewayError) as refused:
        await _run("wrong-password")

    assert refused.value.code == "rekey_twofa_invalid"
    assert _files(rig) == {"acc.session": "old"}


@pytest.mark.asyncio
async def test_a_login_that_lands_on_another_user_is_undone(rig: _Rig) -> None:
    rig.new.user_id = 999

    with pytest.raises(RekeyGatewayError) as refused:
        await _run()

    assert refused.value.code == "rekey_user_mismatch"
    # The authorization it made is ended, the scratch file removed, the old one untouched.
    assert "log_out" in rig.new.events
    assert _files(rig) == {"acc.session": "old"}


@pytest.mark.asyncio
async def test_a_new_file_that_does_not_work_in_its_place_is_swapped_back(rig: _Rig) -> None:
    rig.check_user = 999

    with pytest.raises(RekeyGatewayError) as refused:
        await _run()

    assert refused.value.code == "rekey_swap_failed"
    assert "log_out" in rig.new.events
    # ``new`` was disconnected before the swap: the logout needs a live connection, so it
    # reconnects first or the new authorization would be left standing.
    assert rig.new.events.index("connect") < rig.new.events.index("log_out")
    assert _files(rig) == {"acc.session": "old"}


@pytest.mark.asyncio
async def test_a_file_already_at_the_scratch_name_is_never_signed_in_over(rig: _Rig) -> None:
    _write(rig.scratch, "someone else")

    with pytest.raises(RekeyGatewayError) as refused:
        await _run()

    assert refused.value.code == "rekey_session_conflict"
    assert _files(rig) == {"acc.session": "old", "acc_rekey.session": "someone else"}
    assert rig.new.events == []


@pytest.mark.parametrize(
    ("raised", "code"),
    [
        (errors.AuthTokenExpiredError(request=None), "rekey_token_rejected"),
        (errors.AuthTokenAlreadyAcceptedError(request=None), "rekey_token_rejected"),
        (errors.FloodWaitError(request=None, capture=9), "rekey_flood_wait"),
        (TimeoutError(), "rekey_timeout"),
        (ConnectionError(), "rekey_unavailable"),
        (errors.RPCError(request=None, message="X", code=400), "rekey_failed"),
    ],
)
@pytest.mark.asyncio
async def test_a_telegram_refusal_is_a_bounded_code_and_changes_nothing(
    rig: _Rig, raised: Exception, code: str
) -> None:
    rig.new.wait_failure = raised

    with pytest.raises(RekeyGatewayError) as refused:
        await _run()

    assert refused.value.code == code
    assert _files(rig) == {"acc.session": "old"}


@pytest.mark.asyncio
async def test_an_imported_session_that_is_dead_cannot_approve(rig: _Rig) -> None:
    rig.approver.failure = errors.SessionRevokedError(request=None)

    with pytest.raises(RekeyGatewayError) as refused:
        await _run()

    assert refused.value.code == "rekey_session_dead"
    assert rig.new.events == []
    assert _files(rig) == {"acc.session": "old"}


@pytest.mark.asyncio
async def test_an_approver_that_cannot_say_who_it_is_is_refused(rig: _Rig) -> None:
    rig.approver.user_id = None

    with pytest.raises(RekeyGatewayError) as refused:
        await _run()

    assert refused.value.code == "rekey_approver_unavailable"


@pytest.mark.asyncio
async def test_a_connection_still_open_on_the_imported_file_does_not_stop_the_swap(
    rig: _Rig,
) -> None:
    # Telethon reopens its session file on any call, so a background task that kept the
    # evicted client leaves a connection behind. On Windows a rename then fails (WinError 32).
    leftover = sqlite3.connect(rig.final)
    leftover.execute("select v from marker").fetchall()
    try:
        await _run()
    finally:
        leftover.close()

    assert _files(rig) == {"acc.session": "new"}


@pytest.mark.asyncio
async def test_a_file_that_stays_locked_is_refused_and_keeps_the_imported_session(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_rekey, "_LOCK_WAIT_SECONDS", 0.05)
    holder = sqlite3.connect(rig.final, isolation_level=None)
    holder.execute("begin immediate")  # a writer that never lets go
    try:
        with pytest.raises(RekeyGatewayError) as refused:
            await _run()
    finally:
        holder.close()

    assert refused.value.code == "rekey_swap_failed"
    assert _read(rig.final) == "old"
    assert not rig.scratch.exists()
