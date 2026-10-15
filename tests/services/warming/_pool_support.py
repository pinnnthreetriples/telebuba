"""Shared fakes for the channel-pool search tests: accounts, a fake Telegram, a fake pool.

Every Telegram read is answered at the warming seam from ``CHANNELS``; no real account
or session is touched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.db import create_account, update_account_from_session_check, upsert_warming_state
from core.telegram_client import TelegramReadError
from schemas.accounts import AccountCreate
from schemas.telegram_action_results import LinkedDiscussionGroupResult
from schemas.telegram_actions_activity import LastPostResult
from schemas.telegram_actions_discovery import TelegramChannelMatch, TelegramChannelMatches
from schemas.telegram_session import TelegramSessionCheckResult
from schemas.warming import WarmingStateWrite

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pydantic import BaseModel

NOW = datetime.now(UTC)
MX_PHONE = "+525512345678"

# handle -> (title, subscribers, about, days since the last post)
CHANNELS: dict[str, tuple[str, int, str, int]] = {
    "futbol_mx": ("Fútbol de México hoy", 40_000, "noticias del fútbol mexicano", 1),
    "tiny_mx": ("Recetas de la abuela", 300, "recetas caseras", 1),
    "huge_mx": ("Noticias nacionales", 2_000_000, "todas las noticias", 1),
    "casino_mx": ("Casino ganancias", 50_000, "apuestas", 1),
    "dead_mx": ("Cocina mexicana", 9_000, "recetas tradicionales", 90),
    "ru_news": ("Новости спорта России", 30_000, "все о спорте в России", 1),
    "cine_mx": ("Cine y series", 15_000, "estrenos de la semana", 2),
}


async def seed_account(account_id: str, *, phone: str | None = None) -> None:
    """An alive account with a session, as a session check leaves it."""
    await create_account(AccountCreate(account_id=account_id, phone=phone, session_name=account_id))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id=account_id,
            session_path=account_id,
            status="alive",
            is_temporary=False,
            user_id=1,
            phone=phone,
        ),
    )


async def seed_warming(account_id: str) -> None:
    """An account that is being warmed right now."""
    await seed_account(account_id)
    await upsert_warming_state(
        WarmingStateWrite(
            account_id=account_id,
            state="sleeping",
            started_at=NOW.isoformat(),
            target_days=30,
        ),
    )


@dataclass
class FakeTelegram:
    """Answers the search reads from ``CHANNELS`` and records who read what."""

    reads: list[tuple[str, str]] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    flood_on: str | None = None
    flood_seconds: int | None = 120
    # Runs before every answer: a test hooks its own checks (or a block) in here.
    during: Callable[[str], Awaitable[None]] | None = None

    async def execute_read(self, account_id: str, action: object) -> BaseModel:
        kind = getattr(action, "action_type", "")
        self.reads.append((account_id, kind))
        if kind == "search_channels":
            self.queries.append(getattr(action, "query", ""))
        if self.during is not None:
            await self.during(account_id)
        if kind == self.flood_on:
            reason = "FloodWait"
            raise TelegramReadError(reason, kind="flood_wait", seconds=self.flood_seconds)
        if kind == "search_channels":
            return TelegramChannelMatches(
                items=[
                    TelegramChannelMatch(username=h, title=t[0], kind="channel")
                    for h, t in CHANNELS.items()
                    if h != "cine_mx"
                ]
            )
        if kind == "get_similar_channels":
            return TelegramChannelMatches(
                items=[TelegramChannelMatch(username="cine_mx", title="Cine y series")]
            )
        handle = getattr(action, "channel", "@").lstrip("@")
        _title, subscribers, about, days = CHANNELS[handle]
        if kind == "get_linked_discussion_group":
            return LinkedDiscussionGroupResult(
                comments_enabled=False, participants_count=subscribers, about=about
            )
        return LastPostResult(last_post_at=(NOW - timedelta(days=days)).isoformat())

    async def owned_read(self, account_id: str, action: object) -> BaseModel:
        return await self.execute_read(account_id, action)

    def kinds(self) -> list[str]:
        return [kind for _account, kind in self.reads]

    def accounts(self) -> set[str]:
        return {account for account, _kind in self.reads}
