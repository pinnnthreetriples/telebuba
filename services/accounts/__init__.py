"""Business logic for the accounts domain.

Pure async functions: validate input, talk to ``core/*`` adapters, return
Pydantic models. No NiceGUI, no SQLAlchemy, no Telethon — those live in
``core/*``. UI handlers in ``features/accounts.py`` are thin pass-throughs.

Account lifecycle + actions live here in the package root so the ``core``
collaborators they call (``execute``, ``check_telegram_session``,
``convert_tdata_zip``, ``check_proxy_connectivity``) stay patchable as
``services.accounts.<name>``. Pure rendering helpers live in :mod:`._table`,
upload validation in :mod:`._uploads`.

Per non-negotiable #11, callers in ``features/`` and in any future scheduler
take this module's public functions directly.
"""

from __future__ import annotations

import asyncio
import shutil
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    create_account,
    delete_account,
    fetch_account,
    fetch_account_proxy_settings,
    fetch_device_fingerprint,
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
from core.phone_geo import evaluate_geo
from core.proxy_check import check_proxy_connectivity
from core.tdata_import import convert_tdata_zip
from core.telegram_client import check_telegram_session, execute
from schemas.accounts import (
    AccountCheckRequest,
    AccountCreate,
    AccountProfileUpdateRequest,
    AccountRead,
    AccountSessionFileImport,
)
from schemas.geo import GeoMatch
from schemas.proxy import AccountProxyCheckUpdate
from schemas.telegram_actions import (
    ActionResult,
    AddProfileMusic,
    PostStory,
    SetProfilePhoto,
    UpdateProfile,
)
from schemas.telegram_session import TelegramSessionCheckRequest
from services.accounts._table import load_accounts_table
from services.accounts._uploads import (
    _PROFILE_MUSIC_SUFFIXES,
    _PROFILE_PHOTO_SUFFIXES,
    _STORY_IMAGE_SUFFIXES,
    _STORY_VIDEO_SUFFIXES,
    _session_filename,
    _validate_upload,
    _write_session_file,
)

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

__all__ = [
    "add_account",
    "add_account_profile_music",
    "check_account_proxy",
    "check_account_session",
    "delete_account_proxy",
    "evaluate_account_geo",
    "import_account_session",
    "import_account_tdata",
    "load_accounts_table",
    "post_account_story",
    "save_account_proxy",
    "set_account_profile_photo",
    "update_account_profile",
]


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


class SessionAlreadyExistsError(ValueError):
    """Raised when an import would overwrite an existing account's session.

    A ``.session`` file is effectively a Telegram credential — re-uploading a
    file with the same name must not silently replace what is already there.
    The operator has to delete the existing account first if they really want
    to swap credentials.
    """


async def import_account_session(data: AccountSessionFileImport) -> AccountRead:
    # Service-layer guardrail: ``.session`` files are effectively credentials.
    # The UI may attempt to validate size first, but a CLI / scheduler caller
    # can bypass that — re-check here.
    max_bytes = settings.profile_media.session_max_bytes
    if not data.content:
        msg = "Session file is empty"
        raise ValueError(msg)
    if len(data.content) > max_bytes:
        msg = f"Session file is too large (>{max_bytes} bytes)"
        raise ValueError(msg)
    filename = _session_filename(data.filename)
    session_name = Path(filename).stem
    session_path = settings.telegram.session_dir / filename
    # Refuse to overwrite credentials. Check by account_id (DB) AND by file
    # presence on disk — either being present means there is already an
    # account whose session we would clobber.
    if await fetch_account(session_name) is not None or session_path.exists():
        msg = (
            f"An account with session {session_name!r} already exists. "
            "Delete it first if you want to replace the credentials."
        )
        raise SessionAlreadyExistsError(msg)
    await asyncio.to_thread(_write_session_file, session_path, data.content)
    return await add_account(
        AccountCreate(account_id=session_name, label=data.label, session_name=session_name),
    )


@dataclass(frozen=True)
class _TdataAccountPlan:
    account_id: str
    session_name: str
    staging_path: Path
    final_path: Path


async def _preflight_tdata_plans(plans: list[_TdataAccountPlan]) -> None:
    """Refuse to start the import if any account_id / file would clobber existing state."""
    for plan in plans:
        if await fetch_account(plan.account_id) is not None:
            msg = f"tdata account {plan.account_id!r} already exists. Delete it before importing."
            raise SessionAlreadyExistsError(msg)
        if plan.final_path.exists() and plan.staging_path.resolve() != plan.final_path.resolve():
            msg = (
                f"session file {plan.final_path.name!r} already exists. Delete it before importing."
            )
            raise SessionAlreadyExistsError(msg)


async def _rollback_tdata_import(account_ids: list[str], session_files: list[Path]) -> None:
    """Best-effort: remove DB rows + .session files written during a failed import."""
    for account_id in account_ids:
        with suppress(Exception):
            await delete_account(account_id)
    for session_file in session_files:
        with suppress(OSError):
            session_file.unlink()
    await log_event(
        "WARNING",
        "tdata_import_rolled_back",
        extra={"accounts": account_ids, "files": [str(p) for p in session_files]},
    )


async def import_account_tdata(data: TdataConvertRequest) -> list[AccountRead]:
    """Atomic conversion of a tdata.zip into ``.session`` files + DB rows.

    Convert runs first (staging-only, no side effects on the final dir). Then
    preflight checks that every produced account_id is free in both the DB and
    the final sessions dir; if any conflict, the import aborts before touching
    the final dir. Finally, files are moved and accounts added one-by-one — on
    any mid-batch failure, every change made so far is rolled back so the
    caller never observes a partial import.
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

    plans: list[_TdataAccountPlan] = []
    for summary in result.accounts:
        staging_path = Path(summary.session_path)
        session_name = staging_path.stem
        account_id = str(summary.user_id) if summary.user_id is not None else session_name
        final_path = settings.telegram.session_dir / staging_path.name
        plans.append(_TdataAccountPlan(account_id, session_name, staging_path, final_path))

    await _preflight_tdata_plans(plans)

    placed_files: list[Path] = []
    added_account_ids: list[str] = []
    checked: list[AccountRead] = []
    try:
        for plan in plans:
            if plan.staging_path.resolve() != plan.final_path.resolve():
                plan.final_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(plan.staging_path), str(plan.final_path))
            placed_files.append(plan.final_path)
            await add_account(
                AccountCreate(
                    account_id=plan.account_id,
                    label=data.label or plan.account_id,
                    session_name=plan.session_name,
                ),
            )
            added_account_ids.append(plan.account_id)
            checked.append(
                await check_account_session(AccountCheckRequest(account_id=plan.account_id)),
            )
    except Exception:
        await _rollback_tdata_import(added_account_ids, placed_files)
        raise

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
            asn=result.asn,
            is_datacenter=result.is_datacenter,
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


async def evaluate_account_geo(account_id: str) -> GeoMatch:
    """Non-blocking geo check: does the account's proxy country match its number?

    Compares the phone number's country (via ``phonenumbers``) against the proxy
    exit country, plus the device language region. A mismatch is a warning + risk
    signal for the UI, never a hard block (product decision).
    """
    account = await fetch_account(account_id)
    if account is None:
        return GeoMatch(status="unknown", message="account not found")
    fingerprint = await fetch_device_fingerprint(account_id)
    lang_code = fingerprint.system_lang_code if fingerprint else None
    return evaluate_geo(
        phone=account.phone,
        proxy_country=account.proxy_country_code,
        lang_code=lang_code,
    )
