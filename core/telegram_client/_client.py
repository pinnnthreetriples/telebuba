"""Telethon client construction + lifecycle — the only place clients are built."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from anyio import Path
from telethon import TelegramClient

from core.config import settings
from core.db import fetch_account, fetch_account_proxy_settings
from core.device_fingerprint import get_or_create_device_fingerprint
from schemas.device_fingerprint import TelegramClientProfile, TelegramClientRequest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from schemas.telegram_session import TelegramSessionCheckRequest


async def _session_path(request: TelegramClientRequest) -> str:
    """Resolve the ``.session`` file this account's credentials actually live in.

    A caller that names the session wins. Otherwise the stored ``session_name``
    decides, because the pool — the borrower behind every warming, neurocomment
    and dialog action — keys on ``account_id`` alone and passes no name.

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


async def prepare_telegram_client_profile(
    request: TelegramClientRequest,
) -> TelegramClientProfile:
    await _ensure_session_dir()
    device = await get_or_create_device_fingerprint(request.account_id)
    proxy = await fetch_account_proxy_settings(request.account_id)
    return TelegramClientProfile(
        account_id=request.account_id,
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
    await Path(settings.telegram.session_dir).mkdir(parents=True, exist_ok=True)


def create_telegram_client(profile: TelegramClientProfile) -> TelegramClient:
    device = profile.device
    proxy = _proxy_config(profile)
    if proxy is not None:
        client = TelegramClient(
            profile.session_path,
            settings.telegram.api_id,
            settings.telegram.api_hash,
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
            proxy=proxy,
        )
    else:
        client = TelegramClient(
            profile.session_path,
            settings.telegram.api_id,
            settings.telegram.api_hash,
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
        )
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
