"""Campaign policy: CRUD, the whole-dialog settings save, and who is busy elsewhere.

Every refusal is a stable ``ChatBroadcastRefusalCode``; ``api/`` maps the error class
to the HTTP status. A save is refused while a run is attached — the engine reads the
settings once per run, and a change under it would describe a run that is not the one
playing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings
from core.db import list_accounts_by_ids, list_warming_account_ids
from core.repositories import chat_broadcast as repository
from core.repositories.neurocomment import (
    get_listener_account_id,
    get_listener_running,
    list_active_campaign_account_names,
)
from core.repositories.neuroshilling import list_running_campaign_account_names
from schemas.chat_broadcast import (
    MAX_CAPTION,
    ChatBroadcastCampaign,
    ChatBroadcastCampaigns,
    ChatBroadcastSettings,
    ChatBroadcastSettingsRead,
    ChatBroadcastSettingsUpdate,
)
from services import _account_owner
from services.chat_broadcast import _state
from services.chat_broadcast._errors import (
    ACCOUNT_NOT_FOUND,
    CAMPAIGN_CHANGED,
    CAMPAIGN_RUNNING,
    CAPTION_TOO_LONG,
    INVALID_POST_LINK,
    INVALID_TARGET,
    TOO_MANY_ACCOUNTS,
    TOO_MANY_TARGETS,
    ChatBroadcastConflictError,
    ChatBroadcastInvalidError,
)
from services.chat_broadcast.links import parse_post, parse_target, split_targets

if TYPE_CHECKING:
    from schemas.chat_broadcast_board import ChatBroadcastBusyOwner
    from schemas.chat_broadcast_records import CampaignRecord

_LIVE_STATUSES = frozenset({"running", "stopping"})


def settings_of(record: CampaignRecord) -> ChatBroadcastSettings:
    """The stored settings, or the defaults for a campaign never saved."""
    return ChatBroadcastSettings.model_validate_json(record.settings_json or "{}")


def to_campaign(record: CampaignRecord) -> ChatBroadcastCampaign:
    parsed = settings_of(record)
    return ChatBroadcastCampaign(
        campaign_id=record.campaign_id,
        name=record.name,
        status=record.status,
        target_mode=parsed.target_mode,
        account_count=record.account_count,
        target_count=record.target_count or _planned_targets(parsed),
        round=record.round,
        rest_until=_instant(record.rest_until_unix),
        last_error=record.last_error,
        created_at=datetime.fromisoformat(record.created_at),
        updated_at=record.updated_at,
    )


def _instant(unix: int | None) -> datetime | None:
    return None if unix is None else datetime.fromtimestamp(unix, UTC)


def _planned_targets(parsed: ChatBroadcastSettings) -> int:
    """Before the first run materialises its chats, the count is the list's."""
    return 0 if parsed.target_mode == "own" else len(split_targets(parsed.targets))


async def list_campaigns() -> ChatBroadcastCampaigns:
    return ChatBroadcastCampaigns(
        items=[to_campaign(record) for record in await repository.list_campaigns()]
    )


async def create_campaign(name: str) -> ChatBroadcastCampaign:
    return to_campaign(await repository.create_campaign(name.strip()))


async def delete_campaign(campaign_id: str) -> bool | None:
    """``None`` when there is no such campaign; refused while a run is attached."""
    record = await repository.fetch_campaign(campaign_id)
    if record is None:
        return None
    if record.status in _LIVE_STATUSES:
        raise ChatBroadcastConflictError(CAMPAIGN_RUNNING)
    deleted = await repository.delete_campaign(campaign_id)
    _state.forget_campaign(campaign_id)
    return deleted


async def load_settings(campaign_id: str) -> ChatBroadcastSettingsRead | None:
    record = await repository.fetch_campaign(campaign_id)
    if record is None:
        return None
    roster = await repository.list_roster(campaign_id)
    return ChatBroadcastSettingsRead(
        campaign_id=campaign_id,
        name=record.name,
        status=record.status,
        updated_at=record.updated_at,
        account_ids=[item.account_id for item in roster],
        settings=settings_of(record),
    )


async def save_settings(
    campaign_id: str, update: ChatBroadcastSettingsUpdate
) -> ChatBroadcastSettingsRead | None:
    """One validated write of name, roster and settings, on an unchanged stamp only."""
    if not _state.try_claim_edit(campaign_id):
        raise ChatBroadcastConflictError(CAMPAIGN_RUNNING)
    try:
        record = await repository.fetch_campaign(campaign_id)
        if record is None:
            return None
        if record.status in _LIVE_STATUSES:
            raise ChatBroadcastConflictError(CAMPAIGN_RUNNING)
        if record.updated_at != update.expected_updated_at:
            raise ChatBroadcastConflictError(CAMPAIGN_CHANGED)
        account_ids = await _valid_accounts(update.account_ids)
        cleaned = _valid_settings(update.settings)
        saved = await repository.save_settings(
            campaign_id,
            name=update.name.strip(),
            settings_json=cleaned.model_dump_json(),
            account_ids=account_ids,
            expected_updated_at=update.expected_updated_at,
        )
        if saved is None:
            raise ChatBroadcastConflictError(CAMPAIGN_CHANGED)
    finally:
        _state.finish_edit(campaign_id)
    return await load_settings(campaign_id)


async def _valid_accounts(account_ids: list[str]) -> list[str]:
    unique = list(dict.fromkeys(account_ids))
    if len(unique) > settings.chat_broadcast.max_accounts_per_campaign:
        raise ChatBroadcastInvalidError(TOO_MANY_ACCOUNTS)
    known = {item.account_id for item in (await list_accounts_by_ids(unique)).accounts}
    if any(account_id not in known for account_id in unique):
        raise ChatBroadcastInvalidError(ACCOUNT_NOT_FOUND)
    return unique


def valid_targets(raw_targets: list[str]) -> list[str]:
    """One link per chat, in the order given; refuses a link no run could use."""
    limits = settings.chat_broadcast
    targets: dict[str, str] = {}
    for raw in split_targets(raw_targets):
        parsed = parse_target(raw, max_length=limits.max_target_length)
        if parsed is None:
            raise ChatBroadcastInvalidError(INVALID_TARGET)
        targets.setdefault(parsed.key, raw)
    if len(targets) > limits.max_targets_per_campaign:
        raise ChatBroadcastInvalidError(TOO_MANY_TARGETS)
    return list(targets.values())


def _valid_settings(value: ChatBroadcastSettings) -> ChatBroadcastSettings:
    """Normalise the chat list and refuse what no run could use."""
    targets = valid_targets(value.targets)
    for message in value.messages:
        if message.kind == "post" and message.post.strip() and parse_post(message.post) is None:
            raise ChatBroadcastInvalidError(INVALID_POST_LINK)
        if message.kind == "text" and message.photo is not None and len(message.text) > MAX_CAPTION:
            raise ChatBroadcastInvalidError(CAPTION_TOO_LONG)
    if any(not peer.isdigit() for peer in value.own_excluded):
        raise ChatBroadcastInvalidError(INVALID_TARGET)
    return value.model_copy(update={"targets": targets})


async def busy_owners(
    campaign_id: str, account_ids: list[str]
) -> dict[str, ChatBroadcastBusyOwner]:
    """Which of ``account_ids`` another feature is driving right now.

    The sources neuroshilling's picker reads, plus the discovery claim: the registry
    while runs are in flight, and the durable rows that back it up. This campaign's own
    hold is not "busy".
    """
    from services.neurocomment import _discovery_state  # noqa: PLC0415 - import cycle

    wanted = set(account_ids)
    busy: dict[str, ChatBroadcastBusyOwner] = {}
    for account_id in await list_active_campaign_account_names():
        busy[account_id] = "neurocomment"
    if await get_listener_running() and (listener := await get_listener_account_id()):
        busy[listener] = "neurocomment"
    for account_id in await list_warming_account_ids():
        busy[account_id] = "warming"
    for account_id in await list_running_campaign_account_names():
        busy[account_id] = "neuroshilling"
    for account_id, (holder, _name) in (await repository.list_running_account_names()).items():
        if holder != campaign_id:
            busy[account_id] = "chat_broadcast"
    for account_id, owner in _account_owner.owners().items():
        if owner == "chat_broadcast" and _account_owner.holder_of(account_id) == campaign_id:
            continue
        busy[account_id] = owner
    for account_id in [a for a in wanted if _discovery_state.account_busy(a)]:
        busy[account_id] = "discovery"
    return {account_id: owner for account_id, owner in busy.items() if account_id in wanted}
