from __future__ import annotations

import pathlib
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from python_socks import ProxyConnectionError
from telethon import errors

from core.db import (
    configure_database,
    create_account,
    insert_device_fingerprint,
    list_device_fingerprints_by_ids,
)
from core.device_fingerprint import (
    generate_random_device_fingerprint,
    get_or_create_device_fingerprint,
)
from core.phone_geo import evaluate_geo
from core.telegram_client import (
    check_telegram_session,
    create_telegram_client,
    prepare_telegram_client_profile,
    remove_account_session,
    telegram_client,
)
from core.telegram_client._client import _session_dir_child
from schemas.device_fingerprint import (
    DeviceFingerprint,
    TelegramClientProfile,
    TelegramClientRequest,
)
from schemas.telegram_session import TelegramSessionCheckRequest
from tests.factories import (
    AccountCreateFactory,
    DeviceFingerprintFactory,
    seed_account_proxy,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_device_fingerprint_created_once_in_sqlite(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")

    first = await get_or_create_device_fingerprint("account-1")
    second = await get_or_create_device_fingerprint("account-1")

    assert isinstance(first, DeviceFingerprint)
    assert second == first


@pytest.mark.asyncio
async def test_insert_duplicate_device_fingerprint_returns_saved_row(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")
    saved = DeviceFingerprintFactory.build(account_id="account-duplicate")
    changed = saved.model_copy(update={"device_model": "Laptop"})

    first = await insert_device_fingerprint(saved)
    second = await insert_device_fingerprint(changed)

    assert first == saved
    assert second == saved


@pytest.mark.asyncio
async def test_list_device_fingerprints_by_ids_scopes_and_guards_empty(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await insert_device_fingerprint(DeviceFingerprintFactory.build(account_id="acc-1"))
    await insert_device_fingerprint(DeviceFingerprintFactory.build(account_id="acc-2"))

    scoped = await list_device_fingerprints_by_ids(["acc-1"])

    assert set(scoped) == {"acc-1"}
    assert await list_device_fingerprints_by_ids([]) == {}


@pytest.mark.asyncio
async def test_telegram_client_profile_uses_saved_fingerprint(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")

    request = TelegramClientRequest(account_id="account-2", receive_updates=False)
    first = await prepare_telegram_client_profile(request)
    second = await prepare_telegram_client_profile(request)

    assert first.device == second.device
    assert first.session_path == str(tmp_path / "sessions" / "account-2")
    assert first.receive_updates is False


@pytest.mark.asyncio
async def test_telegram_client_profile_honours_the_stored_session_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A pooled borrower passes no session name, so the account row must decide.

    The pool keys on ``account_id`` only. If that name were used while the
    credential sat in a differently-named file, every pooled action would
    connect to a fresh empty session and report a healthy account unauthorized.
    """
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")
    await create_account(
        AccountCreateFactory.build(account_id="12345", session_name="tdata_0"),
    )

    profile = await prepare_telegram_client_profile(
        TelegramClientRequest(account_id="12345"),
    )

    assert profile.session_path == str(tmp_path / "sessions" / "tdata_0")


@pytest.mark.asyncio
async def test_telegram_client_profile_prefers_an_explicit_session_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The login flows name the session themselves — that still wins over the row."""
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")
    await create_account(
        AccountCreateFactory.build(account_id="12345", session_name="tdata_0"),
    )

    profile = await prepare_telegram_client_profile(
        TelegramClientRequest(account_id="12345", session_name="explicit"),
    )

    assert profile.session_path == str(tmp_path / "sessions" / "explicit")


# The guard's contract as data: a session name must be one plain child component.
# Every name here is refused on EVERY platform and whether or not the sessions
# directory exists, because the refusal is now reached lexically. The trailing-space
# and repeated-dot forms are in the list because they are exactly where a
# ``resolve()``-only verdict diverged (see the two tests below).
_NON_CHILD_NAMES = (
    ".",
    "..",
    "...",
    "....",
    ".. ",
    ". ",
    "../evil",
    "..\\evil",
    "/abs",
    "sub/x",
)


def test_session_name_refusal_never_reaches_the_filesystem(
    tmp_path: Path,
    monkeypatch,
) -> None:
    r"""Property: the verdict is a pure function of the NAME — no OS, no filesystem.

    Deciding by comparing ``candidate.resolve().parent`` to the session dir made the
    answer platform-specific. Win32 strips trailing dots and reads ``\`` as a
    separator; POSIX does neither, so ``"..."`` resolved to ``sessions/...`` and
    ``"..\evil"`` to ``sessions/..\evil`` there — ordinary filenames INSIDE the
    directory, hence ALLOWED. Both are refused on Windows. Nothing escaped on POSIX,
    but the same name got two verdicts, and every CI job runs ``ubuntu-latest``.

    Asserting that ``resolve()`` is never even called is what makes that
    platform-independent: a predicate that reads only the string cannot diverge.
    The second half checks the resolved-parent comparison is still there as the
    symlink backstop for a name the lexical rules accept.

    Pre-fix this fails on the FIRST name on both platforms: the old guard called
    ``resolve()`` before deciding anything, so the exploding patch below fired.
    """
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")

    def _explode(_self: pathlib.Path) -> pathlib.Path:
        msg = "the guard must decide lexically, before any resolve()"
        raise AssertionError(msg)

    monkeypatch.setattr(pathlib.Path, "resolve", _explode)

    for name in _NON_CHILD_NAMES:
        with pytest.raises(ValueError, match="escapes the session directory"):
            _session_dir_child(name)
    with pytest.raises(AssertionError, match="before any resolve"):
        _session_dir_child("acc-1")


@pytest.mark.parametrize("name", _NON_CHILD_NAMES)
@pytest.mark.asyncio
async def test_session_name_refusal_survives_a_missing_sessions_dir(
    tmp_path: Path,
    monkeypatch,
    name: str,
) -> None:
    r"""The same verdict with the sessions directory ABSENT — the guard was fs-stateful.

    ``resolve()`` cannot collapse a ``".."`` component against a directory that is
    not there, so with the dir missing (the relative ``sessions`` default on first
    boot, or one someone deleted) ``"..."``, ``".. "`` and ``". "`` were ALLOWED
    where the very same names were refused once it existed.

    ``remove_account_session`` is the reacher that makes this concrete: it is the one
    caller that gets to the sink without ``_ensure_session_dir()`` running first —
    and it UNLINKS. Pre-fix this fails on Windows for ``"..."``/``".. "``/``". "``
    and on POSIX for those plus ``"...."`` and ``"..\\evil"``.
    """
    configure_database(tmp_path / "telebuba.db")
    missing = tmp_path / "never-created" / "sessions"
    monkeypatch.setattr("core.config.settings.telegram.session_dir", missing)
    assert not missing.exists()

    with pytest.raises(ValueError, match="escapes the session directory"):
        await remove_account_session("acc-1", name)


@pytest.mark.parametrize("name", [".", "..", "...", "../evil", "..\\evil", "/abs"])
@pytest.mark.asyncio
async def test_session_path_refuses_a_name_outside_the_session_dir(
    tmp_path: Path,
    monkeypatch,
    name: str,
) -> None:
    r"""The shared sink, not the charset, is what closes the traversal.

    ``Path`` DROPS a "." component, so ``session_dir / "."`` collapses to the
    directory itself and ``_auth``'s ``Path(f"{session_path}.session")`` then names
    ``<parent>/sessions.session`` — one level UP, beside the database. Every
    Telethon open and the DELETE unlink both come through here, so a name that
    does not resolve to a direct child is refused here rather than at each entry.
    """
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")

    with pytest.raises(ValueError, match="escapes the session directory"):
        await prepare_telegram_client_profile(
            TelegramClientRequest(account_id="acc-1", session_name=name),
        )


@pytest.mark.asyncio
async def test_session_path_refuses_a_dot_account_id_with_no_row(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The missing-row fallback is the other way an unvalidated id reached disk.

    With no account row ``_session_path`` composes ``session_dir / account_id``,
    and Telethon's ``SQLiteSession`` CREATES the file it is handed — which is how
    a spam-check on ``account_id="."`` minted ``<parent>/sessions.session``.
    """
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")

    with pytest.raises(ValueError, match="escapes the session directory"):
        await prepare_telegram_client_profile(TelegramClientRequest(account_id="."))


@pytest.mark.asyncio
async def test_telegram_client_profile_includes_saved_proxy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")
    await create_account(AccountCreateFactory.build(account_id="account-proxy"))
    await seed_account_proxy(
        "account-proxy",
        port=9050,
        username="alice",
        password="secret",
    )

    profile = await prepare_telegram_client_profile(
        TelegramClientRequest(account_id="account-proxy"),
    )

    assert profile.proxy_type == "socks5"
    assert profile.proxy_host == "127.0.0.1"
    assert profile.proxy_port == 9050
    assert profile.proxy_username == "alice"
    assert profile.proxy_password == "secret"


def test_generate_random_device_fingerprint_supports_desktop_platforms(monkeypatch) -> None:
    platforms = iter(("windows", "macos", "linux"))

    def choose(options):
        if {"windows", "macos", "linux"}.issubset(set(options)):
            return next(platforms)
        return options[0]

    monkeypatch.setattr("core.device_fingerprint.secrets.choice", choose)

    assert generate_random_device_fingerprint("windows-account").platform == "windows"
    assert generate_random_device_fingerprint("macos-account").platform == "macos"
    assert generate_random_device_fingerprint("linux-account").platform == "linux"


def test_fingerprint_language_follows_the_phone_country() -> None:
    fingerprint = generate_random_device_fingerprint("ru-account", phone="+79161234567")

    assert fingerprint.lang_code == "ru"
    assert fingerprint.system_lang_code == "ru-RU"


def test_fingerprint_language_pair_can_never_disagree() -> None:
    """The two fields were drawn independently, so one draw could not catch it.

    ``lang_code="en"`` beside ``system_lang_code="ko-KR"`` was reachable but
    unlikely; only repeated draws per country make the old code fail here.
    """
    for phone in ("+79161234567", "+4915112345678", "+819012345678", "+380671234567", None):
        for _ in range(40):
            fingerprint = generate_random_device_fingerprint("acc", phone=phone)
            language, _, region = fingerprint.system_lang_code.partition("-")
            assert region
            assert fingerprint.lang_code == language


def test_fingerprint_language_satisfies_the_geo_evaluation() -> None:
    """Asserted against the consumer: ``evaluate_geo`` is what reads the tag back."""
    phone = "+4915112345678"
    fingerprint = generate_random_device_fingerprint("de-account", phone=phone)

    verdict = evaluate_geo(
        phone=phone,
        proxy_country="DE",
        lang_code=fingerprint.system_lang_code,
    )

    assert verdict.lang_matches is True


def test_fingerprint_language_falls_back_to_english_when_the_country_is_unknown() -> None:
    absent = generate_random_device_fingerprint("no-phone")
    unrecognised = generate_random_device_fingerprint("odd-phone", phone="+9999999")

    assert (absent.lang_code, absent.system_lang_code) == ("en", "en-US")
    assert (unrecognised.lang_code, unrecognised.system_lang_code) == ("en", "en-US")


@pytest.mark.asyncio
async def test_created_fingerprint_language_follows_the_stored_phone(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await create_account(
        AccountCreateFactory.build(account_id="12345", phone="+79161234567"),
    )

    fingerprint = await get_or_create_device_fingerprint("12345")

    assert fingerprint.lang_code == "ru"
    assert fingerprint.system_lang_code == "ru-RU"


def test_create_telegram_client_passes_device_profile(monkeypatch) -> None:
    captured = {}

    class FakeTelegramClient:
        def __init__(self, session_path: str, api_id: int, api_hash: str, **kwargs) -> None:
            captured["session_path"] = session_path
            captured["api_id"] = api_id
            captured["api_hash"] = api_hash
            captured["kwargs"] = kwargs

    monkeypatch.setattr("core.telegram_client._client.TelegramClient", FakeTelegramClient)
    monkeypatch.setattr("core.config.settings.telegram.api_id", 12345)
    monkeypatch.setattr("core.config.settings.telegram.api_hash", "hash")

    client_profile = DeviceFingerprintFactory.build(account_id="account-3")
    created = create_telegram_client(
        TelegramClientProfile(
            account_id="account-3",
            session_path="sessions/account-3",
            receive_updates=True,
            device=client_profile,
        ),
    )

    assert isinstance(created, FakeTelegramClient)
    assert captured == {
        "session_path": "sessions/account-3",
        "api_id": 12345,
        "api_hash": "hash",
        "kwargs": {
            "device_model": "Desktop",
            "system_version": "Windows 11",
            "app_version": "5.4.0 x64",
            "lang_code": "en",
            "system_lang_code": "en-US",
            "receive_updates": True,
            "timeout": 20,
            "connection_retries": 3,
            "retry_delay": 2,
            "request_retries": 3,
            "flood_sleep_threshold": 0,
        },
    }


def test_create_telegram_client_passes_proxy(monkeypatch) -> None:
    captured = {}

    class FakeTelegramClient:
        def __init__(self, _session_path: str, _api_id: int, _api_hash: str, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("core.telegram_client._client.TelegramClient", FakeTelegramClient)

    client_profile = DeviceFingerprintFactory.build(account_id="account-3")
    create_telegram_client(
        TelegramClientProfile(
            account_id="account-3",
            session_path="sessions/account-3",
            receive_updates=True,
            device=client_profile,
            proxy_type="https",
            proxy_host="proxy.local",
            proxy_port=8080,
            proxy_username="bob",
            proxy_password="pw",
        ),
    )

    assert captured["proxy"] == {
        "proxy_type": "http",
        "addr": "proxy.local",
        "port": 8080,
        "rdns": True,
        "username": "bob",
        "password": "pw",
    }


@pytest.mark.parametrize("with_proxy", [False, True])
def test_create_telegram_client_disables_markdown_parsing(
    tmp_path: Path,
    monkeypatch,
    *,
    with_proxy: bool,
) -> None:
    """Both build branches must ship parsing off (a REAL Telethon client).

    Telethon defaults ``parse_mode`` to markdown, which eats ``__bold__``,
    `` `code` ``, ``~~strike~~``, ``**stars**`` and ``[text](url)`` out of every
    operator-authored send — and a channel post read back, prefilled and
    re-saved persists the degraded text.

    Scope: ``create_telegram_client`` is the only client builder on the SENDING
    paths (pool, login, ``telegram_client``), which is what this pins. It is not
    every Telethon client in the process — ``core.tdata_import`` gets one back
    from opentele2's ``ToTelethon`` — but that one only converts credentials and
    never sends, so its parse mode is irrelevant.
    """
    monkeypatch.setattr("core.config.settings.telegram.api_id", 12345)
    monkeypatch.setattr("core.config.settings.telegram.api_hash", "hash")

    client = create_telegram_client(
        TelegramClientProfile(
            account_id="account-parse",
            session_path=str(tmp_path / "account-parse"),
            receive_updates=False,
            device=DeviceFingerprintFactory.build(account_id="account-parse"),
            proxy_type="socks5" if with_proxy else None,
            proxy_host="proxy.local" if with_proxy else None,
            proxy_port=1080 if with_proxy else None,
        ),
    )
    try:
        assert client.parse_mode is None
    finally:
        client.session.close()  # ty: ignore[unresolved-attribute]


@pytest.mark.asyncio
async def test_telegram_client_context_disconnects(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")
    disconnected = False

    class FakeTelegramClient:
        async def disconnect(self) -> None:
            nonlocal disconnected
            disconnected = True

    monkeypatch.setattr(
        "core.telegram_client._client.create_telegram_client",
        lambda _: FakeTelegramClient(),
    )

    async with telegram_client(TelegramClientRequest(account_id="account-4")) as client:
        assert isinstance(client, FakeTelegramClient)

    assert disconnected is True


def _patch_session_pool(monkeypatch, fake_client: FakeSessionClient) -> None:
    """Serve the check from the pool, the way production does.

    The check borrows the account's pooled client instead of building its own —
    two Telethon clients on one ``.session`` file collide on its SQLite lock. A
    connect failure therefore surfaces out of the pool, not out of the client,
    so ``connect_error`` is raised here.
    """

    async def fake_get_client(_account_id: str) -> FakeSessionClient:
        if fake_client.connect_error is not None:
            raise fake_client.connect_error
        return fake_client

    monkeypatch.setattr("core.telegram_client._session.get_client", fake_get_client)


@pytest.mark.asyncio
async def test_check_telegram_session_returns_alive(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")
    monkeypatch.setattr("core.config.settings.telegram.api_id", 12345)
    monkeypatch.setattr("core.config.settings.telegram.api_hash", "hash")
    fake_client = FakeSessionClient(authorized=True)
    _patch_session_pool(monkeypatch, fake_client)

    result = await check_telegram_session(TelegramSessionCheckRequest(account_id="account-alive"))

    assert result.status == "alive"
    assert result.is_temporary is False
    assert result.user_id == 123
    assert result.username == "user"


@pytest.mark.asyncio
async def test_check_telegram_session_returns_unauthorized(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")
    monkeypatch.setattr("core.config.settings.telegram.api_id", 12345)
    monkeypatch.setattr("core.config.settings.telegram.api_hash", "hash")
    fake_client = FakeSessionClient(authorized=False)
    _patch_session_pool(monkeypatch, fake_client)

    result = await check_telegram_session(TelegramSessionCheckRequest(account_id="account-dead"))

    assert result.status == "unauthorized"
    assert result.is_temporary is False
    assert result.user_id is None


@pytest.mark.asyncio
async def test_check_telegram_session_detects_frozen_via_app_config(
    tmp_path: Path, monkeypatch
) -> None:
    # get_me() succeeds but the app config carries a non-zero freeze_since_date —
    # the authoritative freeze signal that the authorized-session probe misses.
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")
    monkeypatch.setattr("core.config.settings.telegram.api_id", 12345)
    monkeypatch.setattr("core.config.settings.telegram.api_hash", "hash")
    fake_client = FakeSessionClient(
        app_config={
            "freeze_since_date": 1700000000,
            "freeze_until_date": 1701000000,
            "freeze_appeal_url": "https://t.me/spambot",
        },
    )
    _patch_session_pool(monkeypatch, fake_client)

    result = await check_telegram_session(TelegramSessionCheckRequest(account_id="account-frozen"))

    assert result.status == "frozen"
    assert result.is_temporary is False
    assert result.error_type == "AccountFrozen"
    assert "https://t.me/spambot" in (result.error_message or "")


@pytest.mark.asyncio
async def test_check_telegram_session_alive_when_no_freeze_field(
    tmp_path: Path, monkeypatch
) -> None:
    # get_me() succeeds and the app config has no freeze marker — stays alive.
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")
    monkeypatch.setattr("core.config.settings.telegram.api_id", 12345)
    monkeypatch.setattr("core.config.settings.telegram.api_hash", "hash")
    fake_client = FakeSessionClient(app_config={"some_other_key": 1})
    _patch_session_pool(monkeypatch, fake_client)

    result = await check_telegram_session(TelegramSessionCheckRequest(account_id="account-ok"))

    assert result.status == "alive"
    assert result.is_temporary is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (TimeoutError("timeout"), "network_error"),
        (ProxyConnectionError("proxy down"), "proxy_error"),
        (errors.SessionRevokedError(request=None), "session_error"),
        (errors.UserDeactivatedBanError(request=None), "account_error"),
        (errors.FrozenMethodInvalidError(request=None), "frozen"),
        (errors.FloodWaitError(request=None, capture=42), "flood_wait"),
    ],
)
async def test_check_telegram_session_classifies_errors(
    tmp_path: Path,
    monkeypatch,
    exc: Exception,
    status: str,
) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")
    monkeypatch.setattr("core.config.settings.telegram.api_id", 12345)
    monkeypatch.setattr("core.config.settings.telegram.api_hash", "hash")
    fake_client = FakeSessionClient(connect_error=exc)
    _patch_session_pool(monkeypatch, fake_client)

    result = await check_telegram_session(TelegramSessionCheckRequest(account_id="account-error"))

    assert result.status == status
    assert result.is_temporary is (status in {"network_error", "proxy_error", "flood_wait"})
    assert result.error_type == type(exc).__name__
    if status == "flood_wait":
        assert result.flood_wait_seconds == 42


class FakeTelegramUser:
    id = 123
    phone = "100200300"
    username = "user"
    first_name = "First"
    last_name = "Last"


class _FakeJsonEntry:
    def __init__(self, key: str, value: object) -> None:
        self.key = key
        self.value = SimpleNamespace(value=value)


class _FakeAppConfig:
    def __init__(self, fields: dict[str, object]) -> None:
        self.config = SimpleNamespace(
            value=[_FakeJsonEntry(key, value) for key, value in fields.items()],
        )


class FakeSessionClient:
    def __init__(
        self,
        *,
        authorized: bool = True,
        connect_error: Exception | None = None,
        app_config: dict[str, object] | None = None,
    ) -> None:
        self.authorized = authorized
        self.connect_error = connect_error
        self.app_config = app_config or {}

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def get_me(self) -> FakeTelegramUser:
        return FakeTelegramUser()

    async def download_profile_photo(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def __call__(self, _request: object) -> _FakeAppConfig:
        return _FakeAppConfig(self.app_config)
