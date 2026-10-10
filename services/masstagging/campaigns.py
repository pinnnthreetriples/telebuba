"""Campaign policy: CRUD, the whole-dialog save, and what a campaign still lacks.

``draft`` and ``ready`` are not stored: a campaign nobody started is a draft while it
lacks something (:func:`missing_of`) and ready once it does not. ``resting`` is a running
campaign waiting for tomorrow. Every refusal is a stable ``MasstaggingRefusalCode``;
``api/`` maps the error class to the HTTP status. A save is refused while a run is
attached — the engine reads the settings once per run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings
from core.db import list_accounts_by_ids
from core.repositories import masstagging as repository
from schemas.masstagging import (
    MasstaggingCampaign,
    MasstaggingCampaigns,
    MasstaggingSettings,
    MasstaggingSettingsRead,
    MasstaggingSettingsUpdate,
)
from services.masstagging import _seams, _state
from services.masstagging._errors import (
    ACCOUNT_NOT_FOUND,
    CAMPAIGN_CHANGED,
    CAMPAIGN_RUNNING,
    TOO_MANY_ACCOUNTS,
    TOO_MANY_MEDIA,
    MasstaggingConflictError,
    MasstaggingInvalidError,
)
from services.masstagging._fleet import FleetAccount, fleet_of
from services.masstagging.targets import seeds_of, valid_targets

if TYPE_CHECKING:
    from schemas.masstagging import MasstaggingMissing, MasstaggingStatus
    from schemas.masstagging_records import CampaignRecord

LIVE_STATUSES = frozenset({"running", "stopping"})


def settings_of(record: CampaignRecord) -> MasstaggingSettings:
    """The stored settings, or the defaults for a campaign never saved."""
    return MasstaggingSettings.model_validate_json(record.settings_json or "{}")


def missing_of(
    parsed: MasstaggingSettings, roster: list[str], fleet: dict[str, FleetAccount]
) -> list[MasstaggingMissing]:
    """What keeps the campaign from starting, in the order the operator would fix it."""
    missing: list[MasstaggingMissing] = []
    if not roster:
        missing.append("no_accounts")
    elif all(
        fleet.get(account_id) is not None and fleet[account_id].spamblock for account_id in roster
    ):
        missing.append("all_spamblocked")
    if parsed.target_source != "contacts" and not seeds_of(parsed.targets):
        missing.append("no_targets")
    if parsed.mode == "stories" and not parsed.media:
        missing.append("no_media")
    return missing


def status_of(record: CampaignRecord, missing: list[MasstaggingMissing]) -> MasstaggingStatus:
    status = record.run_status
    if status == "idle":
        return "draft" if missing else "ready"
    if status == "running":
        resting = record.rest_until_unix is not None and record.rest_until_unix > _seams.now()
        return "resting" if resting else "running"
    if status == "stopping":
        return "paused"
    return status


def _instant(unix: int | None) -> datetime | None:
    return None if unix is None else datetime.fromtimestamp(unix, UTC)


def to_campaign(
    record: CampaignRecord, roster: list[str], fleet: dict[str, FleetAccount]
) -> MasstaggingCampaign:
    parsed = settings_of(record)
    missing = missing_of(parsed, roster, fleet)
    planned = 0 if parsed.target_source == "contacts" else len(seeds_of(parsed.targets))
    return MasstaggingCampaign(
        campaign_id=record.campaign_id,
        name=record.name,
        mode=record.mode,
        status=status_of(record, missing),
        target_count=record.target_count or planned,
        account_count=record.account_count,
        missing=missing,
        rest_until=_instant(record.rest_until_unix),
        last_error=record.last_error,
        created_at=datetime.fromisoformat(record.created_at),
        updated_at=record.updated_at,
    )


async def campaign_view(record: CampaignRecord) -> MasstaggingCampaign:
    roster = await repository.list_roster(record.campaign_id)
    return to_campaign(record, roster, await fleet_of(roster))


async def list_campaigns() -> MasstaggingCampaigns:
    records = await repository.list_campaigns()
    rosters = {r.campaign_id: await repository.list_roster(r.campaign_id) for r in records}
    fleet = await fleet_of(sorted({a for roster in rosters.values() for a in roster}))
    return MasstaggingCampaigns(
        items=[to_campaign(r, rosters[r.campaign_id], fleet) for r in records]
    )


async def create_campaign(name: str) -> MasstaggingCampaign:
    return await campaign_view(await repository.create_campaign(name.strip()))


async def delete_campaign(campaign_id: str) -> bool | None:
    """``None`` when there is no such campaign; refused while a run is attached."""
    record = await repository.fetch_campaign(campaign_id)
    if record is None:
        return None
    if record.run_status in LIVE_STATUSES:
        raise MasstaggingConflictError(CAMPAIGN_RUNNING)
    deleted = await repository.delete_campaign(campaign_id)
    _state.forget_campaign(campaign_id)
    return deleted


async def campaign_exists(campaign_id: str) -> bool:
    return await repository.fetch_campaign(campaign_id) is not None


async def load_settings(campaign_id: str) -> MasstaggingSettingsRead | None:
    record = await repository.fetch_campaign(campaign_id)
    if record is None:
        return None
    roster = await repository.list_roster(campaign_id)
    view = to_campaign(record, roster, await fleet_of(roster))
    return MasstaggingSettingsRead(
        campaign_id=campaign_id,
        name=record.name,
        status=view.status,
        updated_at=record.updated_at,
        account_ids=roster,
        settings=settings_of(record),
    )


async def save_settings(
    campaign_id: str, update: MasstaggingSettingsUpdate
) -> MasstaggingSettingsRead | None:
    """One validated write of name, roster and settings, on an unchanged stamp only."""
    if not _state.try_claim_edit(campaign_id):
        raise MasstaggingConflictError(CAMPAIGN_RUNNING)
    try:
        record = await repository.fetch_campaign(campaign_id)
        if record is None:
            return None
        if record.run_status in LIVE_STATUSES:
            raise MasstaggingConflictError(CAMPAIGN_RUNNING)
        if record.updated_at != update.expected_updated_at:
            raise MasstaggingConflictError(CAMPAIGN_CHANGED)
        account_ids = await _valid_accounts(update.account_ids)
        cleaned = _valid_settings(update.settings)
        saved = await repository.save_settings(
            campaign_id,
            name=update.name.strip(),
            mode=cleaned.mode,
            settings_json=cleaned.model_dump_json(),
            account_ids=account_ids,
            expected_updated_at=update.expected_updated_at,
        )
        if saved is None:
            raise MasstaggingConflictError(CAMPAIGN_CHANGED)
    finally:
        _state.finish_edit(campaign_id)
    return await load_settings(campaign_id)


async def _valid_accounts(account_ids: list[str]) -> list[str]:
    unique = list(dict.fromkeys(account_ids))
    if len(unique) > settings.masstagging.max_accounts_per_campaign:
        raise MasstaggingInvalidError(TOO_MANY_ACCOUNTS)
    known = {item.account_id for item in (await list_accounts_by_ids(unique)).accounts}
    if any(account_id not in known for account_id in unique):
        raise MasstaggingInvalidError(ACCOUNT_NOT_FOUND)
    return unique


def _valid_settings(value: MasstaggingSettings) -> MasstaggingSettings:
    if len(value.media) > settings.masstagging.max_media_per_campaign:
        raise MasstaggingInvalidError(TOO_MANY_MEDIA)
    media = list({item.media_id: item for item in value.media}.values())
    return value.model_copy(update={"targets": valid_targets(value.targets), "media": media})
