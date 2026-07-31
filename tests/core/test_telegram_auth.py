"""Gateway tests for the phone-code login + logout RPCs (Telethon faked)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

import pytest
from telethon import errors

from core.db import configure_database
from core.telegram_client import _auth as auth_module
from core.telegram_client import log_out_session, request_phone_code, submit_phone_code
from core.telegram_client._pool import _is_removing
from schemas.device_fingerprint import TelegramClientRequest
from schemas.phone_login import PhoneCodeRequest, PhoneCodeSubmit

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

# The message ``python_socks`` 2.8.1 can really emit — its ONE format string,
# ``'Could not connect to proxy {}:{} [{}]'`` (async_/asyncio/_proxy.py:97). It
# carries no credentials: the SOCKS5 auth failure is the fixed string "Username
# and password authentication failure", and nothing in ``_errors.py`` formats a
# URL. What leaks is the proxy ENDPOINT, plus the ``.session`` path on a session
# fault — an operator's proxy inventory and a server path, on a response body any
# reader of the HTTP 400 sees. So the contract is class-name-only, and these
# fixtures state the real threat rather than an invented credential in a message
# the library cannot produce.
_PROXY_ERROR_TEXT = "Could not connect to proxy 203.0.113.9:1080 [Connection refused]"
_PROXY_SECRETS = ("203.0.113.9", "1080")


@pytest.fixture(autouse=True)
def _isolate_auth_locks() -> Iterator[None]:
    """Give every function-scoped asyncio test locks from its own event loop."""
    auth_module._AUTH_LOCKS.clear()
    yield
    auth_module._AUTH_LOCKS.clear()


class FakeUser:
    id = 555
    phone = "79990001122"
    username = "logged_in"
    first_name = "Code"
    last_name = "Login"


class FakeSent:
    phone_code_hash = "HASH-123"


class FakeAuthClient:
    def __init__(
        self,
        *,
        needs_2fa: bool = False,
        sign_in_error: Exception | None = None,
        send_error: Exception | None = None,
        log_out_error: Exception | None = None,
        on_connect: Callable[[], None] | None = None,
    ) -> None:
        self.needs_2fa = needs_2fa
        self.sign_in_error = sign_in_error
        self.send_error = send_error
        self.log_out_error = log_out_error
        self.on_connect = on_connect
        self.disconnected = False
        self.logged_out = False
        self.password_used = False
        # Set by ``_patch_client``: real Telethon's ``log_out()`` deletes this.
        self.session_file: Path | None = None

    async def connect(self) -> None:
        if self.on_connect is not None:
            self.on_connect()

    async def disconnect(self) -> None:
        self.disconnected = True

    async def send_code_request(self, phone: str) -> FakeSent:  # noqa: ARG002
        if self.send_error is not None:
            raise self.send_error
        return FakeSent()

    async def sign_in(self, **kwargs: object) -> FakeUser:
        if "password" in kwargs:
            self.password_used = True
            return FakeUser()
        if self.sign_in_error is not None:
            raise self.sign_in_error
        if self.needs_2fa:
            raise errors.SessionPasswordNeededError(request=None)
        return FakeUser()

    async def get_me(self) -> FakeUser:
        return FakeUser()

    async def log_out(self) -> bool:
        if self.log_out_error is not None:
            raise self.log_out_error
        self.logged_out = True
        # Telethon's own log_out() ends with ``session.delete()`` — an
        # ``os.remove`` whose OSError it swallows (sqlite.py). The fake has to do
        # the same or it hides both the deletion and the Windows live-handle
        # failure from every test in this file.
        if self.session_file is not None:
            with suppress(OSError):
                self.session_file.unlink()
        return True


def _patch_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, client: FakeAuthClient) -> None:
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")
    monkeypatch.setattr("core.config.settings.telegram.api_id", 12345)
    monkeypatch.setattr("core.config.settings.telegram.api_hash", "hash")
    monkeypatch.setattr("core.telegram_client._auth.create_telegram_client", lambda _: client)
    # Every test here logs in as "acc", which resolves to this file on disk.
    client.session_file = tmp_path / "sessions" / "acc.session"


@pytest.mark.asyncio
async def test_request_phone_code_returns_the_hash(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient()
    _patch_client(monkeypatch, tmp_path, client)

    challenge = await request_phone_code(PhoneCodeRequest(account_id="acc", phone="79990001122"))

    assert challenge.phone_code_hash == "HASH-123"
    assert challenge.error is None
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_request_phone_code_classifies_failure(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient(send_error=errors.FloodWaitError(request=None, capture=30))
    _patch_client(monkeypatch, tmp_path, client)

    challenge = await request_phone_code(PhoneCodeRequest(account_id="acc", phone="79990001122"))

    assert challenge.phone_code_hash == ""
    assert "flood wait" in (challenge.error or "")


@pytest.mark.asyncio
async def test_submit_phone_code_signs_in(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient()
    _patch_client(monkeypatch, tmp_path, client)

    result = await submit_phone_code(
        PhoneCodeSubmit(account_id="acc", phone="79990001122", phone_code_hash="H", code="11111"),
    )

    assert result.status == "alive"
    assert result.user_id == 555
    assert result.username == "logged_in"
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_submit_phone_code_handles_2fa(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient(needs_2fa=True)
    _patch_client(monkeypatch, tmp_path, client)

    result = await submit_phone_code(
        PhoneCodeSubmit(
            account_id="acc",
            phone="79990001122",
            phone_code_hash="H",
            code="11111",
            password="hunter2",
        ),
    )

    assert result.status == "alive"
    assert client.password_used is True


@pytest.mark.asyncio
async def test_submit_phone_code_2fa_required_without_password(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient(needs_2fa=True)
    _patch_client(monkeypatch, tmp_path, client)

    result = await submit_phone_code(
        PhoneCodeSubmit(account_id="acc", phone="79990001122", phone_code_hash="H", code="11111"),
    )

    assert result.status == "unauthorized"
    assert result.error_type == "SessionPasswordNeededError"


@pytest.mark.asyncio
async def test_submit_phone_code_invalid_code(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient(sign_in_error=errors.PhoneCodeInvalidError(request=None))
    _patch_client(monkeypatch, tmp_path, client)

    result = await submit_phone_code(
        PhoneCodeSubmit(account_id="acc", phone="79990001122", phone_code_hash="H", code="00000"),
    )

    assert result.status == "unauthorized"
    assert result.error_type == "PhoneCodeInvalidError"


@pytest.mark.asyncio
async def test_submit_phone_code_classifies_unexpected_error(tmp_path: Path, monkeypatch) -> None:
    """An unclassified sign-in failure (banned / AuthRestart / network) must not escape.

    The siblings request_phone_code / log_out_session already classify any
    Exception; submit_phone_code used to let PhoneNumberBanned / connect
    ConnectionError propagate raw. It must instead return a typed, temporary
    unknown_error result.
    """
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient(sign_in_error=ConnectionError("network went away"))
    _patch_client(monkeypatch, tmp_path, client)

    result = await submit_phone_code(
        PhoneCodeSubmit(account_id="acc", phone="79990001122", phone_code_hash="H", code="11111"),
    )

    assert result.status == "unknown_error"
    assert result.is_temporary is True
    assert result.error_type == "ConnectionError"
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_log_out_session_marks_unauthorized(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient()
    _patch_client(monkeypatch, tmp_path, client)

    result = await log_out_session(TelegramClientRequest(account_id="acc", receive_updates=False))

    assert result.status == "unauthorized"
    assert client.logged_out is True
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_log_out_session_failure_reports_only_the_exception_class(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The last ``str(exc)`` in this module — now the class name, like its sibling.

    ``log_out_session``'s catch-all is a transport arm, so the same threat
    ``_error_result`` bounds against applies: a ``python_socks`` failure stringifies
    with the proxy endpoint. Nothing reads this field today
    (``_end_session`` → ``update_account_from_session_check`` writes only
    status/identity columns), but the sibling field IS read on the submit path, so
    the contract has to hold before someone surfaces it.

    Pre-fix ``error_message`` was ``str(exc)``, so both negative assertions failed.
    """
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient(log_out_error=ConnectionError(_PROXY_ERROR_TEXT))
    _patch_client(monkeypatch, tmp_path, client)

    result = await log_out_session(TelegramClientRequest(account_id="acc", receive_updates=False))

    assert result.status == "unauthorized"
    assert result.error_message == "ConnectionError"
    for secret in _PROXY_SECRETS:
        assert secret not in (result.error_message or "")
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_log_out_session_evicts_pool_before_wiping_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Wiping the .session must evict the pooled client first (Windows unlink guard).

    The pooled client keeps the ``.session`` SQLite handle open; unlinking under
    a live handle raises PermissionError on Windows. Assert eviction happens
    before the file removal, and only when wiping.
    """
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient()
    _patch_client(monkeypatch, tmp_path, client)

    order: list[str] = []

    async def fake_evict(account_id: str) -> None:
        order.append(f"evict:{account_id}")

    async def fake_remove(session_path: str) -> None:  # noqa: ARG001 - path unused in the fake
        order.append("remove")
        assert _is_removing("acc"), "the wipe must run under the pool tombstone"

    # The wipe holds ``removing_client``, which evicts through the pool's own
    # module-level name — patch it there.
    monkeypatch.setattr("core.telegram_client._pool.evict_client", fake_evict)
    monkeypatch.setattr("core.telegram_client._auth._remove_session_file", fake_remove)

    result = await log_out_session(
        TelegramClientRequest(account_id="acc", receive_updates=False),
        wipe_session=True,
    )

    assert result.status == "unauthorized"
    assert order == ["evict:acc", "remove"], "eviction must precede the .session unlink"


@pytest.mark.asyncio
async def test_log_out_session_no_wipe_still_evicts_the_pool(tmp_path: Path, monkeypatch) -> None:
    """A plain logout evicts too — it revokes the auth key the pool has cached.

    This test used to assert the opposite ("no wipe → no eviction, the file
    stays"), on a Telethon behavior that does not exist: ``log_out()`` itself
    calls ``session.delete()``. So a plain logout already removes the file on
    POSIX, and on Windows only fails to because the pooled client holds the
    handle — which is also what leaves a *revoked* client in ``_CLIENTS``. The
    tombstone therefore has to cover the whole logout, not just the wipe branch.
    """
    configure_database(tmp_path / "telebuba.db")
    marks: list[bool] = []
    client = FakeAuthClient(on_connect=lambda: marks.append(_is_removing("acc")))
    _patch_client(monkeypatch, tmp_path, client)
    session_file = tmp_path / "sessions" / "acc.session"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_bytes(b"sqlite session bytes")

    evicted: list[tuple[str, bool]] = []

    async def fake_evict(account_id: str) -> None:
        # Record whether the logout RPC had already run when the eviction landed.
        evicted.append((account_id, client.logged_out))

    monkeypatch.setattr("core.telegram_client._pool.evict_client", fake_evict)

    await log_out_session(
        TelegramClientRequest(account_id="acc", receive_updates=False),
        wipe_session=False,
    )

    assert marks == [True], "the logout RPC itself must run under the tombstone"
    # ``session_file`` is gone after this call, but only because
    # ``FakeAuthClient.log_out`` unlinks it, mirroring Telethon's own
    # ``session.delete()`` — asserting its absence would test the fake. The
    # production facts are the eviction and its ORDER: Telethon deletes the file
    # inside ``log_out()``, and on Windows a live pooled handle is what makes that
    # delete (and ours) fail, so the eviction has to come first.
    assert evicted == [("acc", False)], "evict before the logout revokes the key"
    assert client.logged_out is True


@pytest.mark.asyncio
async def test_request_phone_code_runs_under_the_pool_tombstone(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The login client is a SECOND handle on a ``.session`` the pool may hold open.

    ``check_telegram_session`` pools a client for every account it probes — new
    and unauthorized ones included — and ``_CLIENTS`` never expires, so two
    ``SQLiteSession`` handles land on one file: ``database is locked`` on the
    second writer, and a stale ``auth_key`` read before the first commits.
    """
    configure_database(tmp_path / "telebuba.db")
    marks: list[bool] = []
    client = FakeAuthClient(on_connect=lambda: marks.append(_is_removing("acc")))
    _patch_client(monkeypatch, tmp_path, client)

    evicted: list[str] = []

    async def fake_evict(account_id: str) -> None:
        evicted.append(account_id)

    monkeypatch.setattr("core.telegram_client._pool.evict_client", fake_evict)

    challenge = await request_phone_code(PhoneCodeRequest(account_id="acc", phone="79990001122"))

    assert challenge.phone_code_hash == "HASH-123"
    assert evicted == ["acc"], "the pooled twin must be disconnected first"
    assert marks == [True], "rebuilds must be refused for the whole exchange"


@pytest.mark.asyncio
async def test_submit_phone_code_runs_under_the_pool_tombstone(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Same second-handle hazard as request-code, and here the auth key is rewritten."""
    configure_database(tmp_path / "telebuba.db")
    marks: list[bool] = []
    client = FakeAuthClient(on_connect=lambda: marks.append(_is_removing("acc")))
    _patch_client(monkeypatch, tmp_path, client)

    evicted: list[str] = []

    async def fake_evict(account_id: str) -> None:
        evicted.append(account_id)

    monkeypatch.setattr("core.telegram_client._pool.evict_client", fake_evict)

    result = await submit_phone_code(
        PhoneCodeSubmit(account_id="acc", phone="79990001122", phone_code_hash="H", code="11111"),
    )

    assert result.status == "alive"
    assert evicted == ["acc"]
    assert marks == [True]


@pytest.mark.asyncio
async def test_request_phone_code_error_hides_the_proxy_endpoint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """``challenge.error`` is the HTTP 400 detail — it must be a bounded class name.

    A ``python_socks`` transport failure stringifies with the proxy host and port,
    so ``str(exc)`` here handed the operator's browser (and anyone reading the
    response) a live entry from their proxy inventory.
    """
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient(send_error=OSError(_PROXY_ERROR_TEXT))
    _patch_client(monkeypatch, tmp_path, client)

    challenge = await request_phone_code(PhoneCodeRequest(account_id="acc", phone="79990001122"))

    assert challenge.error == "OSError"
    for secret in _PROXY_SECRETS:
        assert secret not in (challenge.error or "")


@pytest.mark.asyncio
async def test_submit_phone_code_error_hides_the_proxy_endpoint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Same leak on the submit side: ``error_message`` becomes the 400 detail."""
    configure_database(tmp_path / "telebuba.db")
    client = FakeAuthClient(sign_in_error=OSError(_PROXY_ERROR_TEXT))
    _patch_client(monkeypatch, tmp_path, client)

    result = await submit_phone_code(
        PhoneCodeSubmit(account_id="acc", phone="79990001122", phone_code_hash="H", code="11111"),
    )

    assert result.status == "unknown_error"
    assert result.error_type == "OSError"
    assert result.error_message == "OSError"
    for secret in _PROXY_SECRETS:
        assert secret not in (result.error_message or "")


@pytest.mark.asyncio
async def test_two_auth_flows_never_hold_two_clients_on_one_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """``removing_client`` fences POOL rebuilds; it does not fence ``_auth`` vs ``_auth``.

    Measured before the per-account ``_auth`` lock: a logout fired while a
    request-code was in flight reached ``refcount=2, live_clients=2`` — two
    ``SQLiteSession`` handles on one file, which is the ``database is locked`` /
    stale-``auth_key`` hazard the tombstone was added for. The SPA can produce it
    (logout during a code submit, two tabs, request-code then reset-session).
    """
    configure_database(tmp_path / "telebuba.db")
    live: list[object] = []
    peak = 0
    entered = asyncio.Event()
    gate = asyncio.Event()

    class Blocking(FakeAuthClient):
        async def connect(self) -> None:
            nonlocal peak
            live.append(self)
            peak = max(peak, len(live))
            entered.set()
            if len(live) == 1:
                await gate.wait()

        async def disconnect(self) -> None:
            if self in live:
                live.remove(self)

    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")
    monkeypatch.setattr("core.telegram_client._auth.create_telegram_client", lambda _: Blocking())

    first = asyncio.create_task(
        request_phone_code(PhoneCodeRequest(account_id="acc", phone="79990001122")),
    )
    await entered.wait()
    second = asyncio.create_task(
        log_out_session(TelegramClientRequest(account_id="acc", receive_updates=False)),
    )
    # Long enough for the logout to get through its own profile prep and reach the
    # client build — pre-fix it did, and connected: peak was 2.
    await asyncio.sleep(0.2)

    assert peak == 1, "the second flow must wait for the first, not open a rival handle"
    gate.set()
    await asyncio.gather(first, second)
    assert peak == 1
