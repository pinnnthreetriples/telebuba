"""A scripted Telegram, a movable clock and a campaign seeder for the masstagging tests.

Everything goes through the real repository and services; only ``_seams`` is faked, so
the tests describe what the engine does with each Telegram answer.
"""

from __future__ import annotations

import asyncio
import io
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from PIL import Image
from sqlalchemy import update

from core._schema_tables import _accounts
from core.db import _get_engine, create_account
from core.repositories.accounts import update_account_status
from schemas.accounts import AccountCreate
from schemas.masstagging import MasstaggingSettings, MasstaggingSettingsUpdate
from schemas.telegram_actions import ActionResult, PostStory
from schemas.telegram_actions_masstagging import (
    ListMasstagContacts,
    MasstagContactsResult,
    MasstagResolvedTarget,
    MasstagToggleTtl,
    MasstagUser,
    ResolveMasstagTarget,
)
from services import masstagging as service
from services.masstagging.pace import next_day_start

if TYPE_CHECKING:
    from pydantic import BaseModel

    from core.telegram_client import TelegramReadError
    from schemas.telegram_action_results import ActionStatus
    from schemas.telegram_actions import TelegramAction, TelegramReadAction


class Clock:
    def __init__(self) -> None:
        # Starts as a masstagging day begins: from the wall clock, a run shortly before
        # DAY_START_HOUR split one day's work across two (CI near 09:00 failed).
        self.value = next_day_start(int(time.time()))

    def now(self) -> int:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += max(1, int(seconds))


def answer(status: ActionStatus = "ok", **fields: Any) -> ActionResult:
    return ActionResult(status=status, action_type="masstag_toggle_ttl", account_id="x", **fields)


@dataclass
class Story:
    account_id: str
    mentions: list[str]
    caption: str
    link: str | None
    period: int
    privacy: str
    filename: str = ""


@dataclass
class FakeTelegram:
    """Every username resolves to a person unless scripted; writes succeed unless scripted."""

    toggles: list[tuple[str, str]] = field(default_factory=list)
    stories: list[Story] = field(default_factory=list)
    resolves: list[tuple[str, str]] = field(default_factory=list)
    # account -> answers for its toggles / stories, consumed in order, then success.
    toggle_answers: dict[str, list[ActionResult]] = field(default_factory=dict)
    story_answers: dict[str, list[ActionResult]] = field(default_factory=dict)
    # target -> what a lookup answers (default: a person with that username).
    lookups: dict[str, MasstagResolvedTarget] = field(default_factory=dict)
    # Lookups that raise (a flood on the read side).
    lookup_errors: dict[str, TelegramReadError] = field(default_factory=dict)
    contacts: dict[str, list[MasstagUser]] = field(default_factory=dict)
    clock: Clock | None = None
    # While set and not yet fired, every write waits for it: a run held open mid-flight.
    hold: asyncio.Event | None = None
    # Set as soon as a write reaches the fake (and before any hold).
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    _ids: dict[str, int] = field(default_factory=dict)

    def user_id(self, name: str) -> int:
        return self._ids.setdefault(name.lower(), 1000 + len(self._ids))

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        self.entered.set()
        if self.hold is not None:
            await self.hold.wait()
        match action:
            case MasstagToggleTtl():
                queue = self.toggle_answers.get(account_id)
                result = queue.pop(0) if queue else answer()
                if result.status == "ok":
                    self.toggles.append((account_id, action.peer))
                return result
            case PostStory():
                queue = self.story_answers.get(account_id)
                result = queue.pop(0) if queue else answer(message_id=len(self.stories) + 1)
                if result.status == "ok":
                    self.stories.append(
                        Story(
                            account_id=account_id,
                            mentions=[m.username or m.first_name for m in action.mentions],
                            caption=action.caption or "",
                            link=action.link.url if action.link else None,
                            period=action.period_seconds,
                            privacy=action.privacy_preset,
                            filename=action.filename,
                        )
                    )
                return result
            case _:  # pragma: no cover - the engine writes nothing else
                raise AssertionError(action)

    async def execute_read(self, account_id: str, action: TelegramReadAction) -> BaseModel:
        match action:
            case ResolveMasstagTarget():
                self.resolves.append((account_id, action.target))
                if action.target in self.lookup_errors:
                    raise self.lookup_errors.pop(action.target)
                if action.target in self.lookups:
                    return self.lookups[action.target]
                if action.target.startswith("+"):
                    return MasstagResolvedTarget(outcome="not_found")
                user = MasstagUser(user_id=self.user_id(action.target), username=action.target)
                return MasstagResolvedTarget(outcome="user", user=user)
            case ListMasstagContacts():
                return MasstagContactsResult(users=self.contacts.get(account_id, []))
            case _:  # pragma: no cover - the engine reads nothing else
                raise AssertionError(action)

    def tagged(self) -> list[str]:
        return [name for story in self.stories for name in story.mentions]


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    return buffer.getvalue()


async def account(account_id: str, *, premium: bool = True, alive: bool = True) -> None:
    await create_account(
        AccountCreate(account_id=account_id, label=account_id, session_name=account_id)
    )
    if alive:
        await update_account_status(account_id, status="alive")
    with _get_engine().begin() as connection:
        connection.execute(
            update(_accounts).where(_accounts.c.account_id == account_id).values(premium=premium)
        )


async def seed(
    *,
    accounts: tuple[str, ...] = ("a1",),
    targets: tuple[str, ...] = ("@alpha", "@beta"),
    premium: bool = True,
    **overrides: Any,
) -> str:
    """A saved campaign with a roster and a list; defaults avoid every pause and the ramp."""
    for account_id in accounts:
        await account(account_id, premium=premium)
    created = await service.create_campaign("Spring")
    media = await service.upload_media("story.png", png())
    values: dict[str, Any] = {
        "targets": list(targets),
        "media": [media.model_dump()],
        "ramp_up": False,
        "story_gap_minutes": {"min": 0, "max": 0},
        "dialog_gap_seconds": {"min": 0, "max": 0},
        "stop_tags": 0,
        "prime_first": False,
    }
    values |= overrides
    saved = await service.save_settings(
        created.campaign_id,
        MasstaggingSettingsUpdate(
            expected_updated_at=created.updated_at,
            name="Spring",
            account_ids=list(accounts),
            settings=MasstaggingSettings.model_validate(values),
        ),
    )
    assert saved is not None
    return created.campaign_id


async def run_to_end(campaign_id: str) -> None:
    """Start (or continue) the campaign and wait for its run task to settle."""
    from services.masstagging import _runtime  # noqa: PLC0415 - test-only reach-in

    assert await service.start_campaign(campaign_id)
    task = _runtime._TASKS.get(campaign_id)
    if task is not None:
        await task
