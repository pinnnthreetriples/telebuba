"""Shared helpers for warming service tests."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    create_account,
    save_warming_settings,
    update_account_from_session_check,
    update_proxy_check,
    upsert_warming_state,
)
from schemas.accounts import AccountCreate, AccountRead
from schemas.proxy import ProxyCheckUpdate
from schemas.spam_status import SpamStatusKind, SpamStatusVerdict
from schemas.telegram_actions import ActionResult, TelegramAction
from schemas.telegram_session import TelegramSessionCheckResult
from schemas.warming import (
    AddChannelsRequest,
    WarmingChannel,
    WarmingStateWrite,
)
from services import warming
from services.dialogues import assign_pairs
from tests.factories import seed_account_proxy

if TYPE_CHECKING:
    import pytest

_ZERO_DELAY_FIELDS = (
    "action_delay_min_seconds",
    "action_delay_max_seconds",
    "typing_min_seconds",
    "typing_max_seconds",
    "reading_min_seconds",
    "reading_max_seconds",
    "startup_jitter_max_seconds",
    "dm_read_reply_delay_min_seconds",
    "dm_read_reply_delay_max_seconds",
)

_SAT = "2026-06-13"
_MON = "2026-06-15"


class _Recorder:
    """Captures dispatched actions and returns canned results."""

    def __init__(self) -> None:
        self.actions: list[tuple[str, TelegramAction]] = []
        self.flood_on: set[str] = set()
        self.peer_flood_on: set[str] = set()
        # A landed reaction returns the reacted post's id; None models a skip
        # (channel restricts reactions to emoji we can't use). The cycle counts a
        # reaction only when a message_id comes back.
        self.react_message_id: int | None = 1

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        self.actions.append((account_id, action))
        status = "ok"
        if action.action_type in self.flood_on:
            status = "flood_wait"
        elif action.action_type in self.peer_flood_on:
            status = "peer_flood"
        message_id = self.react_message_id if action.action_type == "react_to_post" else None
        return ActionResult(
            status=status,
            action_type=action.action_type,
            account_id=account_id,
            message_id=message_id if status == "ok" else None,
        )

    def types(self) -> list[str]:
        return [action.action_type for _account_id, action in self.actions]


async def _seed_channel() -> None:
    await warming.add_channels(AddChannelsRequest(raw="@channel_one"))


async def _set_settings(
    *, chat: bool, reactions: bool, key: str | None, enforce_readiness: bool = True
) -> None:
    await save_warming_settings(
        inter_account_chat=chat,
        reactions_enabled=reactions,
        enforce_readiness=enforce_readiness,
        gemini_api_key=key,
    )


def _affinity_pool(size: int) -> list[WarmingChannel]:
    return [
        WarmingChannel(channel=f"chan_{i}", created_at="2026-01-01T00:00:00+00:00")
        for i in range(size)
    ]


def _configure_intensity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 1)
    monkeypatch.setattr(settings.warming, "channels_per_cycle_max", 3)
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 36.0)


def _verdict(account_id: str, status: SpamStatusKind) -> SpamStatusVerdict:
    return SpamStatusVerdict(
        account_id=account_id,
        status=status,
        checked_at="2026-06-13T00:00:00+00:00",
    )


async def _seed_two_warming_accounts() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await create_account(AccountCreate(account_id="acc-2"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="acc-2",
            session_path="acc-2",
            status="alive",
            is_temporary=False,
            user_id=999,
        ),
    )
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-2", state="active"))
    await assign_pairs()


async def _fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
    await asyncio.sleep(3600)


async def _no_initial_delay(*_args: object, **_kwargs: object) -> float:
    # ``_initial_delay_seconds`` is now async (it fetches the account tz), so a
    # patch that suppresses the startup wait must be awaitable too.
    return 0.0


class _StatusRecorder:
    """Like ``_Recorder`` but lets each action_type carry its own status."""

    def __init__(self) -> None:
        self.actions: list[tuple[str, TelegramAction]] = []
        self.status_by_type: dict[str, str] = {}
        self.raise_on: set[str] = set()
        self.flood_seconds_by_type: dict[str, int] = {}

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        self.actions.append((account_id, action))
        if action.action_type in self.raise_on:
            msg = f"boom-{action.action_type}"
            raise RuntimeError(msg)
        status = self.status_by_type.get(action.action_type, "ok")
        flood = self.flood_seconds_by_type.get(action.action_type)
        return ActionResult.model_validate(
            {
                "status": status,
                "action_type": action.action_type,
                "account_id": account_id,
                "flood_wait_seconds": flood,
            },
        )

    def types(self) -> list[str]:
        return [a.action_type for _id, a in self.actions]


def _account(**overrides: object) -> AccountRead:
    base: dict[str, object] = {
        "account_id": "acc-1",
        "status": "alive",
        "created_at": "2026-06-12T00:00:00+00:00",
        "updated_at": "2026-06-12T00:00:00+00:00",
    }
    base.update(overrides)
    return AccountRead.model_validate(base)


async def _seed_ready_account(account_id: str = "acc-1") -> None:
    await create_account(AccountCreate(account_id=account_id))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id=account_id,
            session_path=account_id,
            status="alive",
            is_temporary=False,
            user_id=111,
        ),
    )
    proxy_id = await seed_account_proxy(account_id, host="1.2.3.4")
    await update_proxy_check(
        ProxyCheckUpdate(
            proxy_id=proxy_id,
            status="tcp_working",
            exit_ip="9.9.9.9",
            country_code="US",
        ),
    )
    await _seed_channel()


async def _resolve(value):  # type: ignore[no-untyped-def]
    return value


async def fetch_account_helper(account_id: str):  # type: ignore[no-untyped-def]
    from core.db import fetch_account  # noqa: PLC0415

    return await fetch_account(account_id)


async def _exercise_migration_seven(engine) -> None:  # type: ignore[no-untyped-def]
    from core.migrations import apply_migrations  # noqa: PLC0415

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE accounts ("
            "  account_id VARCHAR PRIMARY KEY,"
            "  label VARCHAR,"
            "  session_name VARCHAR,"
            "  status VARCHAR NOT NULL,"
            "  created_at VARCHAR NOT NULL,"
            "  updated_at VARCHAR NOT NULL"
            ")",
        )
        connection.exec_driver_sql(
            "INSERT INTO accounts (account_id, session_name, status, created_at, updated_at) "
            "VALUES ('acc-1', 'shared', 'new', '2026-01-01', '2026-01-01')",
        )
        connection.exec_driver_sql(
            "INSERT INTO accounts (account_id, session_name, status, created_at, updated_at) "
            "VALUES ('acc-2', 'shared', 'new', '2026-01-02', '2026-01-02')",
        )

    # Pretend the previous migrations already ran so #7 fires alone.
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name VARCHAR NOT NULL, "
            "applied_at VARCHAR NOT NULL)",
        )
        # Stamp every non-#7 migration as already applied so the test DB only
        # exercises the new index migration. Append the new version here when
        # adding a migration that touches a table this test does NOT create.
        already_applied = (1, 2, 3, 4, 5, 6, 8, 10, 16)
        for version in already_applied:
            connection.exec_driver_sql(
                "INSERT INTO schema_version VALUES (?, 'stub', '2026-01-01')",
                (version,),
            )

    # Must not raise.
    apply_migrations(engine)

    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT account_id, session_name FROM accounts ORDER BY account_id",
        ).all()
        names = {str(row[0]): row[1] for row in rows}
        # Older row kept the name; the duplicate was nulled.
        assert names["acc-1"] == "shared"
        assert names["acc-2"] is None
        remediations = connection.exec_driver_sql(
            "SELECT account_id FROM schema_remediations WHERE migration = 7",
        ).all()
        assert [r[0] for r in remediations] == ["acc-2"]
