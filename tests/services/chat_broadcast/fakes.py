"""A scripted Telegram, a movable clock and a campaign seeder for the engine tests.

Everything goes through the real repository and services; only ``_seams`` is faked, so
the tests describe what the engine does with each Telegram answer.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.db import create_account
from core.telegram_client import TelegramReadError
from schemas.accounts import AccountCreate
from schemas.chat_broadcast import ChatBroadcastSettings, ChatBroadcastSettingsUpdate
from schemas.telegram_action_results import ReadChatMessagesResult, ResolveChatResult
from schemas.telegram_actions import (
    ActionResult,
    BroadcastForwardPost,
    BroadcastJoinChatlist,
    BroadcastSendMessage,
    CheckChatlist,
    JoinChannel,
    ListWritableGroups,
    ReadChatMessages,
    ResolveChat,
)
from schemas.telegram_actions_broadcast import BroadcastGroup, ChatlistResult, WritableGroupsResult
from services import chat_broadcast as service

if TYPE_CHECKING:
    from pydantic import BaseModel

    from schemas.telegram_action_results import ActionStatus
    from schemas.telegram_actions import TelegramAction, TelegramReadAction
    from schemas.telegram_actions_chat import ChatKind


_NOT_FOUND = "chat_not_found"
_BAD_REQUEST = "RPC: BadRequestError"


class Clock:
    def __init__(self) -> None:
        self.value = int(time.time())

    def now(self) -> int:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += max(1, int(seconds))


def ok(message_id: int | None = None, status: ActionStatus = "ok") -> ActionResult:
    return ActionResult(
        status=status, action_type="broadcast_send_message", account_id="x", message_id=message_id
    )


def refused(status: ActionStatus = "failed", **fields: Any) -> ActionResult:
    return ActionResult(status=status, action_type="join_channel", account_id="x", **fields)


@dataclass
class Sent:
    account_id: str
    chat: str
    text: str
    photo: bool = False
    post: tuple[str, int] | None = None
    typing: float = 0.0


@dataclass
class FakeTelegram:
    """Answers by action; unscripted joins succeed and unscripted sends are delivered."""

    joins: list[tuple[str, str]] = field(default_factory=list)
    sent: list[Sent] = field(default_factory=list)
    # (account, join token) -> answers, consumed in order; the last one repeats.
    join_answers: dict[tuple[str, str], list[ActionResult]] = field(default_factory=dict)
    # account -> answers for its sends, consumed in order, then delivered.
    send_answers: dict[str, list[ActionResult]] = field(default_factory=dict)
    # Join tokens a resolve does not find yet (a pending request), and their kinds.
    hidden: set[str] = field(default_factory=set)
    kinds: dict[str, ChatKind] = field(default_factory=dict)
    groups: dict[str, WritableGroupsResult] = field(default_factory=dict)
    folders: dict[str, ChatlistResult] = field(default_factory=dict)
    deleted: set[int] = field(default_factory=set)
    captcha: str = "no_challenge"
    # Join token -> the clock time an admin approves the request at.
    approve_at: dict[str, int] = field(default_factory=dict)
    clock: Clock | None = None
    # While set and not yet fired, every write waits for it: a run held open mid-flight.
    hold: asyncio.Event | None = None
    # Fired once, after the first delivered message: lets a test act mid-run.
    after_first_send: asyncio.Event | None = None
    _next_id: int = 100

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        if self.hold is not None:
            await self.hold.wait()
        result = self._dispatch(account_id, action)
        first = self.after_first_send
        if first is not None and self.sent and not first.is_set():
            first.set()
            await asyncio.sleep(3600)
        return result

    def _dispatch(self, account_id: str, action: TelegramAction) -> ActionResult:
        match action:
            case JoinChannel():
                return self._join(account_id, action.channel)
            case BroadcastJoinChatlist():
                return self._join(account_id, f"{action.slug}:{action.peer_id}")
            case BroadcastSendMessage():
                record = Sent(
                    account_id,
                    action.chat,
                    action.text,
                    photo=action.photo is not None,
                    typing=action.typing_seconds,
                )
                return self._send(account_id, record)
            case BroadcastForwardPost():
                record = Sent(account_id, action.chat, "", post=(action.channel, action.message_id))
                return self._send(account_id, record)
            case _:  # pragma: no cover - the engine sends nothing else
                raise AssertionError(action)

    def _join(self, account_id: str, token: str) -> ActionResult:
        self.joins.append((account_id, token))
        answers = self.join_answers.get((account_id, token)) or self.join_answers.get(("*", token))
        if not answers:
            return refused("ok")
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def _send(self, account_id: str, record: Sent) -> ActionResult:
        queue = self.send_answers.get(account_id)
        if queue:
            answer = queue.pop(0)
            if answer.status != "ok":
                return answer
        self.sent.append(record)
        self._next_id += 1
        return ok(self._next_id)

    async def execute_read(self, account_id: str, action: TelegramReadAction) -> BaseModel:
        match action:
            case ResolveChat():
                due = self.approve_at.get(action.target)
                if due is not None and self.clock is not None and self.clock.now() >= due:
                    self.hidden.discard(action.target)
                if action.target in self.hidden:
                    raise TelegramReadError(_NOT_FOUND)
                kind: ChatKind = self.kinds.get(action.target, "megagroup")
                return ResolveChatResult(chat_id=abs(hash(action.target)) % 10**9 + 1, kind=kind)
            case ListWritableGroups():
                return self.groups.get(account_id, WritableGroupsResult(groups=[]))
            case CheckChatlist():
                if action.slug not in self.folders:
                    raise TelegramReadError(_BAD_REQUEST)
                return self.folders[action.slug]
            case ReadChatMessages():
                missing = [m for m in action.message_ids if m in self.deleted]
                return ReadChatMessagesResult(messages=[], missing_ids=missing)
            case _:  # pragma: no cover - the engine reads nothing else
                raise AssertionError(action)

    async def solve_challenge(self, _account_id: str, _chat: str, _group_id: int) -> str:
        return self.captcha

    def texts(self) -> list[str]:
        return [item.text for item in self.sent]


def group(peer_id: int, title: str, username: str | None = None) -> BroadcastGroup:
    return BroadcastGroup(peer_id=peer_id, title=title, username=username)


async def seed(
    *,
    accounts: tuple[str, ...] = ("a1", "a2"),
    targets: tuple[str, ...] = ("@alpha", "@beta"),
    messages: tuple[str, ...] = ("Hello {a|b}", "Second"),
    **overrides: Any,
) -> str:
    """A saved campaign with a roster, chats and a chain; defaults avoid every pause."""
    for account_id in accounts:
        await create_account(
            AccountCreate(account_id=account_id, label=account_id, session_name=account_id)
        )
    created = await service.create_campaign("Crypto")
    values: dict[str, Any] = {
        "targets": list(targets),
        "messages": [{"text": text} for text in messages],
        "randomize": False,
        "join_delay_minutes": 0,
        "loop": False,
        "typing": False,
    }
    values |= overrides
    saved = await service.save_settings(
        created.campaign_id,
        ChatBroadcastSettingsUpdate(
            expected_updated_at=created.updated_at,
            name="Crypto",
            account_ids=list(accounts),
            settings=ChatBroadcastSettings.model_validate(values),
        ),
    )
    assert saved is not None
    return created.campaign_id


async def run_to_end(campaign_id: str) -> None:
    """Start the campaign and wait for its run task to settle."""
    from services.chat_broadcast import _runtime  # noqa: PLC0415 - test-only reach-in

    assert await service.start_campaign(campaign_id)
    task = _runtime._TASKS.get(campaign_id)
    if task is not None:
        await task
