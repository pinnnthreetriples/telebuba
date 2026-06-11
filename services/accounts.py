"""Business logic for the accounts domain.

Pure async functions: validate input, talk to ``core/*`` adapters, return
Pydantic models. No NiceGUI, no SQLAlchemy, no Telethon — those live in
``core/*``. UI handlers in ``features/accounts.py`` are thin pass-throughs.

Public API:

- :func:`add_account` — create the account row and provision its immutable
  device fingerprint.
- :func:`import_account_session` — accept a ``.session`` upload, persist it,
  then call ``add_account``.
- :func:`import_account_tdata` — convert a ``tdata.zip`` upload via
  ``core/tdata_import``, register each contained account, run a session check.
- :func:`check_account_session` — re-verify one account against Telegram.
- :func:`load_accounts_table` — render the accounts table state (filter +
  metric tiles) for the UI.

Per non-negotiable #11, callers in ``features/`` and in any future scheduler
take this module's public functions directly.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    create_account,
    fetch_account_proxy_settings,
    list_accounts,
    update_account_from_session_check,
    update_account_profile_snapshot,
    update_account_proxy_check,
    upsert_account_proxy,
)
from core.db import (
    delete_account_proxy as delete_account_proxy_row,
)
from core.device_fingerprint import get_or_create_device_fingerprint
from core.logging import log_event
from core.proxy_check import check_proxy_connectivity
from core.tdata_import import convert_tdata_zip
from core.telegram_client import check_telegram_session, execute
from schemas.accounts import (
    AccountCheckRequest,
    AccountCreate,
    AccountFilter,
    AccountProfileUpdateRequest,
    AccountRead,
    AccountSessionFileImport,
    AccountsTableState,
    AccountStatus,
    AccountSummary,
    AccountTableRow,
    health_for_status,
)
from schemas.proxy import AccountProxyCheckUpdate
from schemas.telegram_actions import (
    ActionResult,
    AddProfileMusic,
    PostStory,
    SetProfilePhoto,
    UpdateProfile,
)
from schemas.telegram_session import TelegramSessionCheckRequest

if TYPE_CHECKING:
    from schemas.profile_media import (
        AccountProfileMusicUpload,
        AccountProfilePhotoUpload,
        AccountStoryUpload,
    )
    from schemas.proxy import (
        AccountProxyCheckRequest,
        AccountProxyDelete,
        AccountProxyRead,
        AccountProxyUpsert,
    )
    from schemas.tdata import TdataConvertRequest


_PERMANENT_ISSUES = {"unauthorized", "session_error", "account_error"}
_TEMPORARY_ISSUES = {"flood_wait", "network_error", "proxy_error", "unknown_error"}
_PROFILE_PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_STORY_IMAGE_SUFFIXES = _PROFILE_PHOTO_SUFFIXES
_STORY_VIDEO_SUFFIXES = {".mp4", ".mov"}
_PROFILE_MUSIC_SUFFIXES = {".mp3", ".m4a"}


async def add_account(data: AccountCreate) -> AccountRead:
    account = await create_account(data)
    await get_or_create_device_fingerprint(account.account_id)
    saved = await list_accounts()
    persisted = next(
        (item for item in saved.accounts if item.account_id == account.account_id),
        account,
    )
    await log_event(
        "INFO",
        "account_added",
        account_id=persisted.account_id,
        extra={"session_name": persisted.session_name},
    )
    return persisted


async def import_account_session(data: AccountSessionFileImport) -> AccountRead:
    filename = _session_filename(data.filename)
    session_name = Path(filename).stem
    session_path = settings.telegram.session_dir / filename
    await asyncio.to_thread(_write_session_file, session_path, data.content)
    return await add_account(
        AccountCreate(account_id=session_name, label=data.label, session_name=session_name),
    )


async def import_account_tdata(data: TdataConvertRequest) -> list[AccountRead]:
    """Convert a tdata.zip payload to one or more .session files and register each account.

    Every successfully written session is added to the DB and immediately session-checked.
    Returns the post-check ``AccountRead`` rows. Raises ``ValueError`` with a human-readable
    message when the conversion itself failed.
    """
    result = await convert_tdata_zip(data, settings.telegram.session_dir)
    if result.status != "ok":
        msg = f"tdata import failed: {result.status}"
        if result.error:
            msg = f"{msg} — {result.error}"
        await log_event(
            "ERROR",
            "tdata_import_failed",
            extra={"status": result.status, "error": result.error},
        )
        raise ValueError(msg)
    if not result.accounts:
        msg = "tdata contained no accounts"
        await log_event("WARNING", "tdata_no_accounts", extra={"filename": data.filename})
        raise ValueError(msg)

    checked: list[AccountRead] = []
    for summary in result.accounts:
        session_name = Path(summary.session_path).stem
        account_id = str(summary.user_id) if summary.user_id is not None else session_name
        await add_account(
            AccountCreate(
                account_id=account_id,
                label=data.label or account_id,
                session_name=session_name,
            ),
        )
        checked.append(
            await check_account_session(AccountCheckRequest(account_id=account_id)),
        )
    await log_event(
        "INFO",
        "tdata_import_completed",
        extra={"imported": len(checked)},
    )
    return checked


async def check_account_session(data: AccountCheckRequest) -> AccountRead:
    accounts = await list_accounts()
    account = next(item for item in accounts.accounts if item.account_id == data.account_id)
    result = await check_telegram_session(
        TelegramSessionCheckRequest(
            account_id=account.account_id,
            session_name=account.session_name,
        ),
    )
    return await update_account_from_session_check(result)


async def save_account_proxy(data: AccountProxyUpsert) -> AccountProxyRead:
    proxy = await upsert_account_proxy(data)
    await log_event(
        "INFO",
        "account_proxy_saved",
        account_id=data.account_id,
        extra={
            "proxy_type": proxy.proxy_type,
            "host": proxy.host,
            "port": proxy.port,
            "has_username": proxy.username is not None,
            "has_password": proxy.has_password,
        },
    )
    return proxy


async def delete_account_proxy(data: AccountProxyDelete) -> None:
    await delete_account_proxy_row(data)
    await log_event("INFO", "account_proxy_deleted", account_id=data.account_id)


async def check_account_proxy(data: AccountProxyCheckRequest) -> AccountProxyRead:
    proxy = await fetch_account_proxy_settings(data.account_id)
    if proxy is None:
        msg = f"Proxy not found for account: {data.account_id}"
        raise ValueError(msg)
    result = await check_proxy_connectivity(proxy)
    saved = await update_account_proxy_check(
        AccountProxyCheckUpdate(
            account_id=data.account_id,
            status=result.status,
            last_error=result.last_error,
            exit_ip=result.exit_ip,
            country_code=result.country_code,
            country_name=result.country_name,
        ),
    )
    await log_event(
        "INFO" if saved.status == "tcp_working" else "WARNING",
        "account_proxy_checked",
        account_id=data.account_id,
        extra={
            "status": saved.status,
            "exit_ip": saved.exit_ip,
            "country_code": saved.country_code,
            "country_name": saved.country_name,
            "last_error": saved.last_error,
        },
    )
    return saved


async def update_account_profile(data: AccountProfileUpdateRequest) -> AccountRead:
    result = await execute(
        data.account_id,
        UpdateProfile(
            first_name=data.first_name,
            last_name=data.last_name,
            username=data.username,
            bio=data.bio,
        ),
    )
    if result.status != "ok":
        msg = result.error_message or result.status
        raise ValueError(msg)
    account = await update_account_profile_snapshot(data)
    await log_event(
        "INFO",
        "account_profile_updated",
        account_id=data.account_id,
        extra={
            "has_last_name": data.last_name is not None,
            "has_username": data.username is not None,
            "has_bio": data.bio is not None,
        },
    )
    return account


async def set_account_profile_photo(data: AccountProfilePhotoUpload) -> ActionResult:
    _validate_upload(
        filename=data.filename,
        content=data.content,
        max_bytes=settings.profile_media.photo_max_bytes,
        allowed_suffixes=_PROFILE_PHOTO_SUFFIXES,
        label="profile photo",
    )
    result = await execute(
        data.account_id,
        SetProfilePhoto(filename=data.filename, content=data.content),
    )
    if result.status != "ok":
        msg = result.error_message or result.status
        raise ValueError(msg)
    await log_event(
        "INFO",
        "account_profile_photo_updated",
        account_id=data.account_id,
        extra={"filename": data.filename},
    )
    return result


async def post_account_story(data: AccountStoryUpload) -> ActionResult:
    max_bytes = (
        settings.profile_media.story_image_max_bytes
        if data.media_kind == "image"
        else settings.profile_media.story_video_max_bytes
    )
    allowed_suffixes = (
        _STORY_IMAGE_SUFFIXES if data.media_kind == "image" else _STORY_VIDEO_SUFFIXES
    )
    _validate_upload(
        filename=data.filename,
        content=data.content,
        max_bytes=max_bytes,
        allowed_suffixes=allowed_suffixes,
        label=f"story {data.media_kind}",
    )
    result = await execute(
        data.account_id,
        PostStory(
            filename=data.filename,
            content=data.content,
            media_kind=data.media_kind,
            caption=data.caption,
            privacy_preset=data.privacy_preset,
            period_seconds=data.period_seconds,
            protect_content=data.protect_content,
        ),
    )
    if result.status != "ok":
        msg = result.error_message or result.status
        raise ValueError(msg)
    await log_event(
        "INFO",
        "account_story_posted",
        account_id=data.account_id,
        extra={
            "filename": data.filename,
            "media_kind": data.media_kind,
            "privacy_preset": data.privacy_preset,
        },
    )
    return result


async def add_account_profile_music(data: AccountProfileMusicUpload) -> ActionResult:
    _validate_upload(
        filename=data.filename,
        content=data.content,
        max_bytes=settings.profile_media.music_max_bytes,
        allowed_suffixes=_PROFILE_MUSIC_SUFFIXES,
        label="profile music",
    )
    result = await execute(
        data.account_id,
        AddProfileMusic(
            filename=data.filename,
            content=data.content,
            title=data.title,
            performer=data.performer,
        ),
    )
    if result.status != "ok":
        msg = result.error_message or result.status
        raise ValueError(msg)
    await log_event(
        "INFO",
        "account_profile_music_added",
        account_id=data.account_id,
        extra={"filename": data.filename, "has_title": data.title is not None},
    )
    return result


async def load_accounts_table(data: AccountFilter) -> AccountsTableState:
    accounts = await list_accounts()
    filtered = [account for account in accounts.accounts if _matches_filter(account, data)]
    return AccountsTableState(
        rows=[_to_table_row(account) for account in filtered],
        summary=_summarize(accounts.accounts),
    )


def _session_filename(filename: str) -> str:
    name = Path(filename).name
    if Path(name).suffix.lower() != ".session":
        msg = "Upload a .session file"
        raise ValueError(msg)
    if not Path(name).stem:
        msg = "Session file name is empty"
        raise ValueError(msg)
    return name


def _write_session_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _validate_upload(
    *,
    filename: str,
    content: bytes,
    max_bytes: int,
    allowed_suffixes: set[str],
    label: str,
) -> None:
    if not content:
        msg = f"{label} file is empty"
        raise ValueError(msg)
    if len(content) > max_bytes:
        msg = f"{label} file is too large"
        raise ValueError(msg)
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed_suffixes:
        allowed = ", ".join(sorted(allowed_suffixes))
        msg = f"{label} must be one of: {allowed}"
        raise ValueError(msg)


def _matches_filter(account: AccountRead, data: AccountFilter) -> bool:
    if data.status not in ("all", account.status):
        return False
    if not data.query:
        return True
    haystack = " ".join(
        value or ""
        for value in (
            account.account_id,
            account.label,
            account.phone,
            account.username,
            account.first_name,
            account.last_name,
            account.session_name,
        )
    ).lower()
    return data.query.lower() in haystack


def _summarize(accounts: list[AccountRead]) -> AccountSummary:
    return AccountSummary(
        total=len(accounts),
        alive=sum(account.status == "alive" for account in accounts),
        permanent_issue=sum(account.status in _PERMANENT_ISSUES for account in accounts),
        temporary_issue=sum(account.status in _TEMPORARY_ISSUES for account in accounts),
        never_checked=sum(account.status == "new" for account in accounts),
    )


def _to_table_row(account: AccountRead) -> AccountTableRow:
    return AccountTableRow(
        account_id=account.account_id,
        label=account.label or account.account_id,
        status=_status_label(account.status),
        health=health_for_status(account.status),
        telegram=_telegram_label(account),
        session=account.session_name or account.account_id,
        device=_device_label(account),
        proxy=_proxy_label(account),
        last_checked=_format_last_checked(account.last_checked_at),
        first_name=account.first_name,
        last_name=account.last_name,
        username=account.username,
        bio=account.bio,
        proxy_type=account.proxy_type,
        proxy_host=account.proxy_host,
        proxy_port=account.proxy_port,
        proxy_status=account.proxy_status,
        proxy_last_checked_at=account.proxy_last_checked_at,
        proxy_last_error=account.proxy_last_error,
        proxy_exit_ip=account.proxy_exit_ip,
        proxy_country_code=account.proxy_country_code,
        proxy_country_name=account.proxy_country_name,
    )


_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600
_SECONDS_PER_DAY = 86_400


def _format_last_checked(iso_value: str | None, now: datetime | None = None) -> str:
    """Render an ISO-8601 timestamp as a compact relative string for the UI.

    Returns "never" when ``iso_value`` is empty. Otherwise produces
    ``Ns ago`` / ``Nm ago`` / ``Nh ago`` / ``Nd ago``. Falls back to the
    raw string if parsing fails — we never want a single bad row to break
    the whole table.
    """
    if not iso_value:
        return "never"
    try:
        moment = datetime.fromisoformat(iso_value)
    except ValueError:
        return iso_value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    reference = now or datetime.now(UTC)
    seconds = max(0, int((reference - moment).total_seconds()))
    if seconds < _SECONDS_PER_MINUTE:
        return f"{seconds}s ago"
    if seconds < _SECONDS_PER_HOUR:
        return f"{seconds // _SECONDS_PER_MINUTE}m ago"
    if seconds < _SECONDS_PER_DAY:
        return f"{seconds // _SECONDS_PER_HOUR}h ago"
    return f"{seconds // _SECONDS_PER_DAY}d ago"


def _status_label(status: AccountStatus) -> str:
    labels = {
        "new": "New",
        "alive": "Alive",
        "unauthorized": "Unauthorized",
        "session_error": "Session error",
        "account_error": "Account error",
        "flood_wait": "Flood wait",
        "network_error": "Network",
        "proxy_error": "Proxy",
        "unknown_error": "Unknown",
    }
    return labels[status]


def _telegram_label(account: AccountRead) -> str:
    name = " ".join(part for part in (account.first_name, account.last_name) if part)
    username = f"@{account.username}" if account.username else ""
    phone = account.phone or ""
    return " | ".join(part for part in (name, username, phone) if part) or "-"


def _device_label(account: AccountRead) -> str:
    return (
        " | ".join(
            part
            for part in (
                account.device_model,
                account.device_system_version,
                account.device_app_version,
            )
            if part
        )
        or "-"
    )


def _proxy_label(account: AccountRead) -> str:
    if not account.proxy_type or not account.proxy_host or account.proxy_port is None:
        return "-"
    return f"{account.proxy_type.upper()} {account.proxy_host}:{account.proxy_port}"
