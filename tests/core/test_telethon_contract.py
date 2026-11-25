"""The Telethon contract: every place our code leans on something Telethon does not promise.

``pyproject.toml`` bounds Telethon to one minor because the code below relies on INTERNAL
behaviour of the library, and every other test talks to fakes: a release could silently break
custody-critical behaviour (the language pack of a mobile session vanishing, the death of an
auth key read as a network error) with the whole suite green. These tests talk to the REAL
installed ``telethon`` (no client ever connects, no network) and fail the day it moves.
When one fails: read the Telethon changelog for sessions/auth, fix the code, only then widen
the bound. This file holds the single list of those places (``INVENTORY``; the tests live
here, in ``test_telethon_contract_errors.py`` and in ``test_telethon_contract_tl.py``, split
only for the 700-line limit); ``test_the_inventory_names_real_tests`` keeps the list and the
tests together, and the scans in ``..._tl.py`` fail when a NEW private dependency appears that
is not listed.

Marked ``(!)`` below: the most fragile, because Telethon can change them in a patch release
and our code would not notice.

Not checkable offline (listed here and in the report, not tested):

* What a live server answers: ``AcceptLoginTokenRequest`` returning an ``Authorization``,
  ``InitPasskeyLoginRequest`` returning options whose ``.options.data`` holds JSON, the
  ``unconfirmed`` flag of a fresh web/phone authorization, the 406/401 texts Telegram really
  sends (only Telethon's text-to-class table is tested, not that Telegram still uses the texts).
* ``TelegramClient.connect()`` re-sending ONE ``InitConnectionRequest`` on every (re)connect
  and on every exported-sender DC switch: needs a socket. Pinned only as a source tripwire.
* The update loop storing ``_updates_error`` and calling ``disconnect()`` when the key dies:
  needs a live connection. Pinned only as a source tripwire.
* The ``opentele2`` tdata converter (``core/tdata_import.py``): a different package that
  builds its own Telethon client; its contract is out of this file's scope.
* ``core.telegram_client._twofa_srp._GOOD_PRIME`` against Telethon's ``good_prime``: a wrong
  value would run Telethon's factorisation for minutes, so it is checked with a bound in
  ``tests/core/telegram_client/test_twofa_srp.py``.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import re
import sqlite3
import tomllib
from typing import TYPE_CHECKING

import pytest
import telethon
from telethon import TelegramClient, errors
from telethon.client import telegrambaseclient, updates, users
from telethon.crypto import AuthKey
from telethon.sessions import MemorySession, SQLiteSession
from telethon.sessions import sqlite as sqlite_session
from telethon.tl import functions

import core.session_vault as vault
from core.config import settings
from core.device_fingerprint_android import ANDROID_LANG_PACK, generate_android_fingerprint
from core.session_file import SESSION_SUFFIX, inspect_session_file, session_file_of
from core.telegram_client import create_telegram_client
from core.telegram_client._client import _proxy_config
from core.telegram_client._guarded_client import (
    GuardedTelegramClient,
    TelegramClientDisconnectedError,
    disconnect_reason,
)
from core.telegram_client._passkey_login import _signed64
from core.telegram_client._rekey_files import copy_session, file_key, key_mark
from core.telegram_client._rekey_undo import _client_key
from schemas.device_fingerprint import DeviceFingerprint, TelegramClientProfile
from tests.core._telethon_contract_support import (
    ROOT,
    open_clients,
    point_settings_at,
    session_of,
    signature,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


@pytest.fixture
def make_client(tmp_path: Path) -> Iterator[Callable[..., TelegramClient]]:
    yield from open_clients(tmp_path)


@pytest.fixture(autouse=True)
def _credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    point_settings_at(monkeypatch, tmp_path)


# (production place, what it relies on, the test that protects it). Most fragile first.
INVENTORY: tuple[tuple[str, str, str], ...] = (
    (
        "core/telegram_client/_client.py create_telegram_client (!)",
        "client._init_request is the ONE InitConnectionRequest Telethon resends; its "
        "lang_pack is set by assignment because the constructor has no such argument",
        "test_mobile_lang_pack_lands_on_the_init_request",
    ),
    (
        "core/telegram_client/_client.py create_telegram_client",
        "TelegramClient.__init__ keyword names (device_model ... flood_sleep_threshold, proxy) "
        "and the proxy dict keys",
        "test_the_constructor_takes_every_keyword_the_factory_passes",
    ),
    (
        "core/telegram_client/_guarded_client.py GuardedTelegramClient._call (!)",
        "UserMethods._call(self, sender, request, ordered, flood_sleep_threshold) is the "
        "single path every request takes; __call__ routes through it",
        "test_the_guard_still_wraps_the_real_call_path",
    ),
    (
        "core/telegram_client/_guarded_client.py disconnect_reason (!)",
        "client._updates_error: set by the update loop when the key dies, None otherwise",
        "test_a_cancelled_request_reads_the_recorded_auth_error",
    ),
    (
        "core/telegram_client/_pool_live.py has_live_client",
        "client._authorized: None unknown / False refused / True signed in",
        "test_authorized_starts_unknown",
    ),
    (
        "core/telegram_client/_passkey_login.py _passkey_steps",
        "client._switch_dc(new_dc) coroutine; session.dc_id, session.auth_key.key_id "
        "(unsigned int)",
        "test_switch_dc_and_the_session_identity_the_passkey_login_reads",
    ),
    (
        "core/telegram_client/_rekey_undo.py _client_key",
        "session.auth_key.key stays readable in memory after the session is closed",
        "test_the_auth_key_stays_in_memory_after_close",
    ),
    (
        "core/session_file.py, core/telegram_client/_rekey_files.py, core/session_vault.py (!)",
        "SQLiteSession file schema: table names, `sessions` columns in order "
        "(dc_id, server_address, port, auth_key, takeout_id, tmp_auth_key), version 8, "
        "file name <path>.session",
        "test_the_session_file_schema",
    ),
    (
        "core/session_file.py inspect_session_file",
        "a fresh file holds no key, a saved one holds one (columns server_address, auth_key)",
        "test_inspect_session_file_reads_what_telethon_wrote",
    ),
    (
        "core/telegram_client/_rekey_files.py file_key / key_mark / copy_session",
        "the sqlite backup copy of a real session reloads in SQLiteSession unchanged",
        "test_the_production_copy_round_trips_a_real_session",
    ),
    (
        "core/session_vault.py snapshot_session / restore_session",
        "an encrypted snapshot restores to a file SQLiteSession loads unchanged",
        "test_the_vault_round_trips_a_real_session",
    ),
    (
        "core/telegram_client/_session.py _SESSION_ERRORS / _ACCOUNT_ERRORS, _session_audit.py "
        "_PROVEN_DEAD_KEY, _profile.py _DEAD_SESSION_ERROR_CODES (!)",
        "which RPC error classes mean a dead key / deactivated account, via the class "
        "hierarchy (UnauthorizedError, AuthKeyError) and the plain-Exception AuthKeyNotFound",
        "test_the_dead_key_sets_keep_their_shape",
    ),
    (
        "core/telegram_client/_authorized.py probe_authorized (!)",
        "UnauthorizedError (HTTP 401) is the whole family that reads 'not signed in'; "
        "AuthKeyDuplicatedError (406) and a flood wait are NOT in it",
        "test_probe_authorized_only_reads_unauthorized_as_false",
    ),
    (
        "core/telegram_client/_session.py, _actions.py, _copy_media.py, _session_audit.py",
        "class hierarchy and HTTP `code` of the errors the ladders order by "
        "(FloodWaitError vs Frozen*, 401/400/406/420/503, TimeoutError is Telethon's 503)",
        "test_the_error_hierarchy_the_ladders_depend_on",
    ),
    (
        "same",
        "Telethon's wire-text -> class table (AUTH_KEY_UNREGISTERED, SESSION_REVOKED, ...)",
        "test_the_wire_text_maps_to_the_class_we_classify_on",
    ),
    (
        "core/telegram_client/_session.py, _session_close.py, _web_login.py, _rekey_token.py",
        "FloodWaitError(.seconds); Authorization.hash/unconfirmed read by getattr",
        "test_flood_wait_carries_seconds",
    ),
    (
        "_session_close.py, _rekey_token.py, _web_login.py, _passkey*.py, _twofa*.py, "
        "_auth.py, _session.py, _session_audit.py",
        "keyword names of the TL requests and types built in custody flows",
        "test_custody_requests_accept_the_keywords_we_pass",
    ),
    (
        "_session_audit.py _session / _listing",
        "attributes read by getattr off Authorization and account.Authorizations",
        "test_the_authorization_attributes_the_audit_reads",
    ),
    (
        "_twofa.py, _twofa_email.py, _twofa_srp.py",
        "account.Password attributes, the ModPow KDF class (salt1, salt2, g, p) and "
        "telethon.password.compute_check / compute_digest signatures",
        "test_the_password_types_and_srp_helpers",
    ),
    (
        "_passkey.py, _passkey_login.py",
        "passkey request/type names, fields and the Passkey / Passkeys attributes read",
        "test_the_passkey_types",
    ),
    (
        "_read_post_image.py _still_byte_count",
        "mirrors the private telethon.utils._photo_size_byte_count",
        "test_the_photo_size_weight_matches_telethons_private_one",
    ),
    (
        "_warm_browse.py",
        "telethon.helpers.add_surrogate / del_surrogate (UTF-16 entity offsets)",
        "test_the_surrogate_helpers_count_utf16_units",
    ),
    (
        "all of api/ core/ services/ tools/",
        "every `telethon` import, `errors.X`, `functions.a.B`, `types.X`, `events.X`, "
        "`utils.X`, `hints.X` still resolves",
        "test_every_telethon_name_the_code_uses_resolves",
    ),
    (
        "all of api/ core/ services/ tools/ (!)",
        "no private Telethon attribute is touched except the listed ones; no import from "
        "a private or deep module except the listed ones",
        "test_no_new_private_telethon_attribute_is_used",
    ),
    (
        "pyproject.toml",
        "the version window the whole file was written against",
        "test_the_installed_telethon_is_inside_the_tested_window",
    ),
)

_UPDATE_NOTICE = (
    "Telethon was updated: re-run this file, read the Telethon changelog for sessions/auth, "
    "then widen the bound in pyproject.toml - this is a custody change."
)
_TESTED_MINOR = (1, 45)


def _profile(tmp_path: Path, *, mobile: bool) -> TelegramClientProfile:
    if mobile:
        device = generate_android_fingerprint("acc", None)
    else:
        device = DeviceFingerprint(
            account_id="acc",
            platform="windows",
            device_model="Desktop",
            system_version="Windows 11",
            app_version="5.6.3 x64",
            lang_code="en",
            system_lang_code="en-US",
        )
    return TelegramClientProfile(
        account_id="acc",
        device_type=device.device_type,
        session_path=str(tmp_path / ("mobile" if mobile else "desktop")),
        receive_updates=False,
        device=device,
    )


def _write_session(path: Path, key: bytes | None = None, *, takeout: int | None = None) -> bytes:
    """A session file written by the real library; the key it holds."""
    key = key or os.urandom(256)
    session = SQLiteSession(str(path))
    session.set_dc(2, "149.154.167.51", 443)
    session.auth_key = AuthKey(key)
    session.takeout_id = takeout
    session.save()
    session.close()
    return key


def _load_session(path: Path) -> SQLiteSession:
    return SQLiteSession(str(path))


# ---------------------------------------------------------------------------------------
# the client we build and the places it is reached into
# ---------------------------------------------------------------------------------------


def test_the_constructor_takes_every_keyword_the_factory_passes(
    tmp_path: Path, make_client: Callable[..., TelegramClient]
) -> None:
    accepted = set(signature(TelegramClient.__init__))
    passed = {
        "device_model",
        "system_version",
        "app_version",
        "lang_code",
        "system_lang_code",
        "receive_updates",
        "timeout",
        "connection_retries",
        "retry_delay",
        "request_retries",
        "flood_sleep_threshold",
        "proxy",
    }
    assert passed <= accepted
    profile = _profile(tmp_path, mobile=False).model_copy(
        update={"proxy_type": "https", "proxy_host": "10.0.0.1", "proxy_port": 3128}
    )
    proxy = _proxy_config(profile)
    assert proxy is not None
    assert set(proxy) == {"proxy_type", "addr", "port", "rdns", "username", "password"}
    client = make_client("proxied", proxy=proxy)
    assert client._proxy == proxy


def test_the_constructor_still_has_no_lang_pack_argument() -> None:
    # The tripwire for the day the assignment below becomes a public argument.
    assert "lang_pack" not in signature(TelegramClient.__init__)


def test_mobile_lang_pack_lands_on_the_init_request(tmp_path: Path) -> None:
    mobile_profile = _profile(tmp_path, mobile=True)
    desktop_profile = _profile(tmp_path, mobile=False)
    mobile = create_telegram_client(mobile_profile)
    desktop = create_telegram_client(desktop_profile)
    try:
        request = mobile._init_request
        assert isinstance(request, functions.InitConnectionRequest)
        assert request.lang_pack == ANDROID_LANG_PACK == "android"
        request.query = functions.help.GetConfigRequest()  # what ``connect`` wraps it around
        assert b"android" in bytes(request)
        assert mobile.session is not desktop.session
        assert desktop._init_request.lang_pack == ""
        for client, profile_device in (
            (desktop, desktop_profile.device),
            (mobile, mobile_profile.device),
        ):
            sent = client._init_request
            assert sent.system_lang_code == profile_device.system_lang_code
            assert sent.lang_code == profile_device.lang_code
            assert sent.system_version == profile_device.system_version
            assert sent.app_version == profile_device.app_version
        assert mobile.parse_mode is None
    finally:
        mobile.session.close()  # ty: ignore[unresolved-attribute]
        desktop.session.close()  # ty: ignore[unresolved-attribute]


def test_telethon_resends_the_one_init_request_object() -> None:
    # Source tripwire (a connection cannot be made offline): the request is built once in
    # ``__init__`` and the same object goes out on every connect and exported-sender switch.
    assert "self._init_request = functions.InitConnectionRequest(" in inspect.getsource(
        telegrambaseclient.TelegramBaseClient.__init__
    )
    # (``connect`` itself is replaced suite-wide by a guard, so the whole module is read.)
    module = inspect.getsource(telegrambaseclient)
    assert "req = self._init_request" in module
    assert "InvokeWithLayerRequest(LAYER, self._init_request)" in module


@pytest.mark.asyncio
async def test_the_guard_still_wraps_the_real_call_path() -> None:
    base = inspect.signature(TelegramClient._call)
    ours = inspect.signature(GuardedTelegramClient._call)
    assert inspect.iscoroutinefunction(TelegramClient._call)
    assert list(ours.parameters) == list(base.parameters)
    assert [p.default for p in ours.parameters.values()] == [
        p.default for p in base.parameters.values()
    ]
    # ``__call__`` -> ``self._call(self._sender, ...)``: a request on a client whose sender
    # loses its answer to a disconnect reaches the guard through Telethon's REAL ``_call``.
    client = GuardedTelegramClient(MemorySession(), 1, "hash")
    loop = asyncio.get_running_loop()

    class Sender:
        def send(self, _request: object, ordered: bool = False) -> asyncio.Future[object]:  # noqa: ARG002, FBT001, FBT002
            future: asyncio.Future[object] = loop.create_future()
            loop.call_soon(future.cancel)  # what MTProtoSender._disconnect() does
            return future

    client._sender = Sender()  # ty: ignore[invalid-assignment]
    with pytest.raises(TelegramClientDisconnectedError):
        await client(functions.updates.GetStateRequest())
    client._updates_error = errors.AuthKeyUnregisteredError(request=None)
    with pytest.raises(errors.AuthKeyUnregisteredError):
        await client(functions.updates.GetStateRequest())


def test_a_cancelled_request_reads_the_recorded_auth_error(
    make_client: Callable[..., TelegramClient],
) -> None:
    client = make_client()
    assert client._updates_error is None
    assert isinstance(disconnect_reason(client), TelegramClientDisconnectedError)
    dead = errors.AuthKeyDuplicatedError(request=None)
    client._updates_error = dead
    assert disconnect_reason(client) is dead
    # Source tripwire: the update loop is what writes it, and ``__init__`` starts it empty.
    assert "self._updates_error = e" in inspect.getsource(updates)
    assert "self._updates_error = None" in inspect.getsource(
        telegrambaseclient.TelegramBaseClient.__init__
    )


def test_authorized_starts_unknown(make_client: Callable[..., TelegramClient]) -> None:
    client = make_client()
    assert client._authorized is None
    assert client.is_connected() is False
    assert inspect.iscoroutinefunction(users.UserMethods.is_user_authorized)
    # ``is_user_authorized`` and ``log_out`` are what move the flag.
    assert "self._authorized = True" in inspect.getsource(users.UserMethods.is_user_authorized)


def test_switch_dc_and_the_session_identity_the_passkey_login_reads(
    tmp_path: Path, make_client: Callable[..., TelegramClient]
) -> None:
    assert inspect.iscoroutinefunction(TelegramClient._switch_dc)
    assert signature(TelegramClient._switch_dc) == ["self", "new_dc"]
    _write_session(tmp_path / "acc")
    client = make_client()
    assert isinstance(session_of(client).dc_id, int)
    assert session_of(client).dc_id == 2
    key_id = session_of(client).auth_key.key_id
    assert isinstance(key_id, int)
    assert 0 <= key_id < 2**64  # unsigned: the TL ``long`` is not, hence ``_signed64``
    assert -(2**63) <= _signed64(key_id) < 2**63
    assert (client.api_id, client.api_hash) == (1, "hash")


def test_the_auth_key_stays_in_memory_after_close(
    tmp_path: Path, make_client: Callable[..., TelegramClient]
) -> None:
    key = _write_session(tmp_path / "acc")
    client = make_client()
    session_of(client).close()
    assert _client_key(client) == key


# ---------------------------------------------------------------------------------------
# the session file on disk
# ---------------------------------------------------------------------------------------


def test_the_session_file_schema(tmp_path: Path) -> None:
    _write_session(tmp_path / "acc", takeout=7)
    path = tmp_path / "acc.session"
    assert sqlite_session.EXTENSION == SESSION_SUFFIX == ".session"
    assert session_file_of(str(tmp_path / "acc")) == path
    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
        columns = [row[1] for row in db.execute("pragma table_info(sessions)")]
        version = db.execute("select version from version").fetchall()
        row = db.execute("select dc_id, server_address, port, takeout_id from sessions").fetchall()
    db.close()
    assert tables >= {"version", "sessions", "entities", "sent_files", "update_state"}
    assert columns == ["dc_id", "server_address", "port", "auth_key", "takeout_id", "tmp_auth_key"]
    assert version == [(8,)]
    assert sqlite_session.CURRENT_VERSION == 8
    assert row == [(2, "149.154.167.51", 443, 7)]


def test_inspect_session_file_reads_what_telethon_wrote(tmp_path: Path) -> None:
    fresh = SQLiteSession(str(tmp_path / "fresh"))
    fresh.close()
    assert inspect_session_file(tmp_path / "fresh.session") == "no_key"
    _write_session(tmp_path / "keyed")
    assert inspect_session_file(tmp_path / "keyed.session") == "ok"


def test_the_production_copy_round_trips_a_real_session(tmp_path: Path) -> None:
    key = _write_session(tmp_path / "src", takeout=9)
    assert file_key(str(tmp_path / "src.session")) == key
    copy_session(str(tmp_path / "src.session"), str(tmp_path / "dst.session"))
    assert file_key(str(tmp_path / "dst.session")) == key
    assert key_mark(str(tmp_path / "dst.session")) == key_mark(str(tmp_path / "src.session"))
    loaded = _load_session(tmp_path / "dst")
    try:
        assert loaded.auth_key.key == key
        assert (loaded.dc_id, loaded.server_address, loaded.port) == (2, "149.154.167.51", 443)
        assert loaded.takeout_id == 9
    finally:
        loaded.close()


def test_the_vault_round_trips_a_real_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.session_vault, "key", vault.new_vault_key())
    key = _write_session(tmp_path / "acc")
    version = vault.snapshot_session(tmp_path / "acc.session")
    assert version is not None
    restored = tmp_path / "restored.session"
    vault.restore_session(version, restored)
    loaded = _load_session(tmp_path / "restored")
    try:
        assert loaded.auth_key.key == key
        assert (loaded.dc_id, loaded.server_address, loaded.port) == (2, "149.154.167.51", 443)
    finally:
        loaded.close()
    # The consistent read the snapshot is made from is a plain SQLite database image.
    image = tmp_path / "image.session"
    image.write_bytes(vault._read_consistent(tmp_path / "acc.session"))
    assert file_key(str(image)) == key


# ---------------------------------------------------------------------------------------
# the window
# ---------------------------------------------------------------------------------------


def test_the_inventory_names_real_tests() -> None:
    modules = [
        globals(),
        vars(importlib.import_module("tests.core.test_telethon_contract_errors")),
        vars(importlib.import_module("tests.core.test_telethon_contract_tl")),
    ]
    missing = [
        name
        for _, _, name in INVENTORY
        if not any(callable(module.get(name)) for module in modules)
    ]
    assert not missing, missing
    assert len({name for _, _, name in INVENTORY}) == len(INVENTORY)


def test_the_installed_telethon_is_inside_the_tested_window() -> None:
    major, minor, *_ = (int(part) for part in re.findall(r"\d+", telethon.__version__)[:3])
    assert (major, minor) == _TESTED_MINOR, f"{telethon.__version__}: {_UPDATE_NOTICE}"
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirement = next(
        r for r in declared["project"]["dependencies"] if r.lower().startswith("telethon")
    )
    assert requirement.replace(" ", "") == "telethon>=1.45.0,<1.46", (
        f"the Telethon requirement lost its upper bound ({requirement}): {_UPDATE_NOTICE}"
    )
