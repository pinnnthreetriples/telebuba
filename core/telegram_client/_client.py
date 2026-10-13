"""Telethon client construction + lifecycle — the only place clients are built."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from anyio import to_thread
from telethon import TelegramClient

from core.config import settings
from core.db import fetch_account, fetch_account_proxy_settings, fetch_device_fingerprint
from core.device_fingerprint import get_or_create_device_fingerprint
from core.device_fingerprint_android import ANDROID_LANG_PACK
from core.secure_paths import make_private_dir
from schemas.device_fingerprint import (
    TelegramClientProfile,
    TelegramClientRequest,
    other_device,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from schemas.accounts import AccountRead
    from schemas.device_fingerprint import DeviceFingerprint, DeviceType
    from schemas.devices import DeviceRefusalCode
    from schemas.telegram_session import TelegramSessionCheckRequest

# Stable codes for a device the client cannot be built for; the SPA translates them.
DEVICE_MISSING: DeviceRefusalCode = "device_missing"
MOBILE_API_NOT_CONFIGURED: DeviceRefusalCode = "mobile_api_not_configured"


class DeviceUnavailableError(ValueError):
    """The requested device cannot connect: the account lacks it, or mobile is not set up.

    Never answered with another device — a desktop session presenting itself for a
    phone the operator chose (or the reverse) is exactly the fallback this refuses.
    """

    def __init__(self, code: DeviceRefusalCode, account_id: str) -> None:
        self.code: DeviceRefusalCode = code
        self.account_id = account_id
        # The message IS the code, like every other gateway ``ValueError``.
        super().__init__(code)


def mobile_api_configured() -> bool:
    return bool(settings.telegram.mobile_api_id and settings.telegram.mobile_api_hash)


def resolve_device(account: AccountRead | None, requested: DeviceType | None) -> DeviceType:
    """The device a client is built for: ``requested``, else the account's primary.

    An account row that is gone resolves to desktop, which is what every pre-device
    caller (a probe racing a removal) always got.
    """
    devices: list[DeviceType] = account.devices if account is not None else ["desktop"]
    device = requested or devices[0]
    if device not in devices:
        raise DeviceUnavailableError(DEVICE_MISSING, account.account_id if account else "")
    return device


async def _session_path(request: TelegramClientRequest) -> str:
    """Resolve the ``.session`` file this account's credentials actually live in.

    A caller that names the session wins. Otherwise the stored ``session_name``
    decides, because the pool — the borrower behind every warming, neurocomment
    and dialog action — keys on ``account_id`` and a device and passes no name.
    The account's OTHER device lives in ``second_session_name``: a second, separate
    authorization, never the same file opened with another fingerprint.

    All three creation paths currently derive ``session_name`` from the same
    value as ``account_id`` (``tdata_import`` names the file after the Telegram
    user id, and ``_tdata`` reads the account id back out of it), and nothing
    updates the column afterwards, so this resolves to the name it always did.
    It reads the row rather than assuming the equality because the failure mode
    is silent: a divergent name would make every pooled action open a file that
    does not exist and mint an empty, unauthorized session next to a perfectly
    good credential.

    Both branches go through :func:`_session_dir_child`, which is the sink that
    keeps a name from escaping the sessions directory.
    """
    if request.session_name:
        return _session_dir_child(request.session_name)
    account = await fetch_account(request.account_id)
    if account is not None and request.device_type not in {None, account.session_device}:
        if account.second_session_name is None:
            raise DeviceUnavailableError(DEVICE_MISSING, request.account_id)
        return _session_dir_child(account.second_session_name)
    stored = account.session_name if account is not None else None
    return _session_dir_child(stored or request.account_id)


# Substrings that disqualify a session name outright, checked BEFORE any
# ``resolve()``: traversal plus either separator. The two literals cover ``os.sep``
# and ``os.altsep`` on both platforms, so the verdict is the same on each. A
# leading "." is checked separately in the guard below.
_FORBIDDEN_NAME_PARTS = ("..", "/", "\\")


def _session_dir_child(name: str) -> str:
    r"""``str(session_dir / name)``, refusing a name that is not a direct child.

    This is the one sink both the unlink (``_auth._remove_session_file``) and
    every Telethon open reach, which is why the check lives here rather than at
    each entry point — the ``account_id`` charset is a second line of defence,
    not the guard.

    ``Path`` DROPS a ``"."`` component, so ``session_dir / "."`` collapses to
    ``session_dir`` itself and ``Path(f"{session_path}.session")`` then names
    ``<parent>/sessions.session`` — one level ABOVE the directory, beside the
    database. ``account_id="."`` was reachable: ``Path("..session").stem`` is
    ``"."`` and the session-file import derives the id from that stem, so a
    DELETE unlinked the file above the directory and Telethon's SQLiteSession
    re-created it on the next probe.

    The verdict is reached LEXICALLY, from the name alone, and the resolved-parent
    comparison is kept only as the symlink backstop. Deciding on ``resolve()``
    alone made the guard depend on two things a contract cannot state:

    * **The platform.** Win32 strips trailing dots and reads ``\\`` as a
      separator, so ``"..."`` and ``"..\\evil"`` resolved to the PARENT there
      while POSIX — every CI job — treats both as ordinary filenames inside the
      directory. Neither escapes on POSIX, so this was a portability wart rather
      than a hole, but the same six names got two different verdicts by OS.
    * **Filesystem state.** ``resolve()`` on a non-existent path cannot collapse
      ``".."`` against a real directory, so with the sessions directory ABSENT
      (the relative ``sessions`` default on first boot, or a directory someone
      deleted) ``"..."``, ``".. "`` and ``". "`` were ALLOWED where the same
      names were refused once it existed. ``_auth.remove_account_session`` is the
      one caller that reaches here without ``_ensure_session_dir()`` first.

    A leading ``"."`` covers ``"."``, ``".."``, ``"..."`` and the trailing-space
    variants Win32 also trims; ``_FORBIDDEN_NAME_PARTS`` covers traversal and
    both separators. What survives is a single plain component, so the value
    returned here and the value the predicate accepted name the same file — no
    ``sub/../x`` can be validated after resolution and then handed to Telethon
    verbatim.
    """
    session_dir = settings.telegram.session_dir
    if not name or name.startswith(".") or any(part in name for part in _FORBIDDEN_NAME_PARTS):
        msg = f"session name escapes the session directory: {name!r}"
        raise ValueError(msg)
    candidate = session_dir / name
    if candidate.resolve().parent != session_dir.resolve():
        msg = f"session name escapes the session directory: {name!r}"
        raise ValueError(msg)
    return str(candidate)


def _device_of(account: AccountRead | None, request: TelegramClientRequest) -> DeviceType | None:
    """The device a NAMED session file belongs to, so it is never opened as the other one.

    The login/logout flows and the session check name the account's own session;
    resolving them to the primary device would open a mobile-only ``.session`` with
    the desktop fingerprint once a desktop is added.
    """
    if account is None or request.session_name is None:
        return None
    if request.session_name == account.second_session_name:
        return other_device(account.session_device)
    return account.session_device if request.session_name == account.session_name else None


async def _device_fingerprint(account_id: str, device_type: DeviceType) -> DeviceFingerprint:
    """Desktop mints lazily as it always did; mobile only ever reads.

    A mobile fingerprint is created where its authorization is (account creation or
    the second-device sign-in), so a missing one is a refusal, not a mint.
    """
    if device_type == "desktop":
        return await get_or_create_device_fingerprint(account_id)
    if not mobile_api_configured():
        raise DeviceUnavailableError(MOBILE_API_NOT_CONFIGURED, account_id)
    fingerprint = await fetch_device_fingerprint(account_id, "mobile")
    if fingerprint is None:
        raise DeviceUnavailableError(DEVICE_MISSING, account_id)
    return fingerprint


async def prepare_telegram_client_profile(
    request: TelegramClientRequest,
    *,
    device: DeviceFingerprint | None = None,
) -> TelegramClientProfile:
    """Everything a client for ``request``'s device needs, credentials resolved in core.

    ``device`` is the second-device sign-in's not-yet-stored fingerprint; every other
    caller reads the stored one.
    """
    await _ensure_session_dir()
    if device is None:
        account = await fetch_account(request.account_id)
        device_type = resolve_device(account, request.device_type or _device_of(account, request))
        device = await _device_fingerprint(request.account_id, device_type)
        request = request.model_copy(update={"device_type": device_type})
    proxy = await fetch_account_proxy_settings(request.account_id)
    return TelegramClientProfile(
        account_id=request.account_id,
        device_type=device.device_type,
        session_path=await _session_path(request),
        receive_updates=request.receive_updates,
        device=device,
        proxy_type=proxy.proxy_type if proxy else None,
        proxy_host=proxy.host if proxy else None,
        proxy_port=proxy.port if proxy else None,
        proxy_username=proxy.username if proxy else None,
        proxy_password=proxy.password if proxy else None,
    )


async def prepare_session_check_profile(
    request: TelegramSessionCheckRequest,
) -> TelegramClientProfile:
    return await prepare_telegram_client_profile(
        TelegramClientRequest(
            account_id=request.account_id,
            session_name=request.session_name,
            receive_updates=False,
        ),
    )


async def _ensure_session_dir() -> None:
    # Owner-only (0700): this is the dir a Telethon client will create the account's
    # ``.session`` credential in, and it is often the first site to create it.
    await to_thread.run_sync(make_private_dir, settings.telegram.session_dir)


def create_telegram_client(profile: TelegramClientProfile) -> TelegramClient:
    device = profile.device
    mobile = device.device_type == "mobile"
    proxy = _proxy_config(profile)
    # Passed only when set: an explicit ``proxy=None`` is not what a proxy-less
    # client was ever built with.
    proxy_option: dict[str, Any] = {"proxy": proxy} if proxy is not None else {}
    client = TelegramClient(
        profile.session_path,
        settings.telegram.mobile_api_id if mobile else settings.telegram.api_id,
        settings.telegram.mobile_api_hash if mobile else settings.telegram.api_hash,
        device_model=device.device_model,
        system_version=device.system_version,
        app_version=device.app_version,
        lang_code=device.lang_code,
        system_lang_code=device.system_lang_code,
        receive_updates=profile.receive_updates,
        timeout=settings.telegram.timeout_seconds,
        connection_retries=settings.telegram.connection_retries,
        retry_delay=settings.telegram.retry_delay_seconds,
        request_retries=settings.telegram.request_retries,
        flood_sleep_threshold=settings.telegram.flood_sleep_threshold,
        **proxy_option,
    )
    if mobile:
        # Telethon 1.x has no constructor argument for ``lang_pack`` and hardcodes ''
        # in the ``InitConnectionRequest`` it builds once and resends on every
        # (re)connect. The official Android app sends "android", so a mobile
        # authorization without it is a mismatch. This sets that one TL field on the
        # request object — the same object Telethon itself mutates in ``set_proxy``
        # and opentele2 sets ``lang_pack`` on — and patches no code.
        client._init_request.lang_pack = ANDROID_LANG_PACK  # noqa: SLF001
    # Telethon defaults ``parse_mode`` to markdown, which silently EATS the
    # metacharacters in every text we send: ``__bold__``, `` `code` ``,
    # ``~~strike~~``, ``**stars**`` and ``[text](url)`` come out stripped, and a
    # channel post read back → prefilled → re-saved persists the degraded text.
    # No send/edit site relies on markdown being interpreted (operator free text
    # and LLM prose), so parsing is off for every client this function builds —
    # the pool, the login flow and the ``telegram_client`` context manager alike.
    # It is NOT every Telethon client in the process: ``core.tdata_import`` gets
    # one back from opentele2's ``ToTelethon``, outside this function. That one
    # only converts credentials and never sends, so it needs no parse mode; the
    # sending surfaces all come through here.
    # ``None`` disables parsing; Telethon's setter is annotated ``str``.
    client.parse_mode = None  # ty: ignore[invalid-assignment]
    return client


@asynccontextmanager
async def telegram_client(request: TelegramClientRequest) -> AsyncIterator[TelegramClient]:
    profile = await prepare_telegram_client_profile(request)
    client = create_telegram_client(profile)
    try:
        yield client
    finally:
        await client.disconnect()


def _proxy_config(profile: TelegramClientProfile) -> dict[str, object] | None:
    if not profile.proxy_type or not profile.proxy_host or profile.proxy_port is None:
        return None
    # Telethon's proxy dict speaks python-socks names: "socks5" / "http". Our
    # internal type uses "https" to match how proxy sellers advertise the
    # protocol — same underlying CONNECT tunnel, just relabelled at the edge.
    telethon_type = "http" if profile.proxy_type == "https" else profile.proxy_type
    return {
        "proxy_type": telethon_type,
        "addr": profile.proxy_host,
        "port": profile.proxy_port,
        "rdns": True,
        "username": profile.proxy_username,
        "password": profile.proxy_password,
    }
