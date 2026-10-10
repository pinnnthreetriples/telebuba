"""A scripted Telegram for the user parser: members, history, comments, invites, failures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.telegram_client import TelegramReadError
from schemas.telegram_action_results import ActionResult, ResolveChatResult
from schemas.telegram_actions import (
    JoinChannel,
    ReadChannelPostReplies,
    ReadChatHistoryAuthors,
    ReadChatParticipants,
    ResolveChat,
)
from schemas.telegram_actions_user_parser import (
    ChatHistoryMessage,
    ParsedTelegramUser,
    ReadChannelPostRepliesResult,
    ReadChatHistoryAuthorsResult,
    ReadChatParticipantsResult,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

    from schemas.telegram_actions import TelegramAction, TelegramReadAction

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)
_NOT_FOUND = "chat_not_found"


def person(user_id: int, **fields: object) -> ParsedTelegramUser:
    return ParsedTelegramUser.model_validate(
        {"user_id": user_id, "first_name": f"U{user_id}", "last_seen": "recently", **fields}
    )


@dataclass
class Post:
    """A message as the fake hands it out: its author and its facts."""

    message_id: int
    author: ParsedTelegramUser | None
    text: str = ""
    hours_ago: float = 1
    is_reply: bool = False
    is_forward: bool = False
    replies: int | None = None

    def record(self) -> ChatHistoryMessage:
        return ChatHistoryMessage(
            message_id=self.message_id,
            sender_id=None if self.author is None else self.author.user_id,
            date=NOW - timedelta(hours=self.hours_ago),
            is_reply=self.is_reply,
            is_forward=self.is_forward,
            replies=self.replies,
            text=self.text,
        )


@dataclass
class Failure:
    error: TelegramReadError
    action_type: str | None = None
    account_id: str | None = None
    times: int = 1


@dataclass
class FakeTelegram:
    members: dict[str, list[ParsedTelegramUser]] = field(default_factory=dict)
    # What Telegram claims the group has, when more than it hands out.
    totals: dict[str, int] = field(default_factory=dict)
    hidden: set[str] = field(default_factory=set)
    admins: dict[str, list[ParsedTelegramUser]] = field(default_factory=dict)
    # chat -> posts newest first; (chat, post id) -> comments newest first.
    history: dict[str, list[Post]] = field(default_factory=dict)
    replies: dict[tuple[str, int], list[Post]] = field(default_factory=dict)
    # Invite token -> the chat id it opens; the accounts already inside each.
    invites: dict[str, int] = field(default_factory=dict)
    inside: set[tuple[str, str]] = field(default_factory=set)
    join_status: dict[str, str] = field(default_factory=dict)
    failures: list[Failure] = field(default_factory=list)
    reads: list[tuple[str, BaseModel]] = field(default_factory=list)
    writes: list[tuple[str, BaseModel]] = field(default_factory=list)

    def fail(
        self,
        error: TelegramReadError,
        *,
        action_type: str | None = None,
        account_id: str | None = None,
        times: int = 1,
    ) -> None:
        self.failures.append(Failure(error, action_type, account_id, times))

    def _raise_if_scripted(self, account_id: str, action: BaseModel) -> None:
        action_type = getattr(action, "action_type", None)
        for failure in self.failures:
            if failure.times <= 0:
                continue
            if failure.action_type not in (None, action_type):
                continue
            if failure.account_id not in (None, account_id):
                continue
            failure.times -= 1
            raise failure.error

    async def execute_read(self, account_id: str, action: TelegramReadAction) -> BaseModel:
        self.reads.append((account_id, action))
        self._raise_if_scripted(account_id, action)
        match action:
            case ResolveChat():
                return self._resolve(account_id, action.target)
            case ReadChatParticipants():
                return self._participants(action)
            case ReadChatHistoryAuthors():
                return self._history(action)
            case ReadChannelPostReplies():
                posts = self.replies.get((action.channel, action.post_id), [])
                page = _after(posts, action.offset_id)[: action.limit]
                return ReadChannelPostRepliesResult(
                    messages=[p.record() for p in page], users=_authors(page)
                )
        raise AssertionError(action)

    def _resolve(self, account_id: str, token: str) -> ResolveChatResult:
        chat_id = self.invites.get(token)
        if chat_id is None or (account_id, token) not in self.inside:
            raise TelegramReadError(_NOT_FOUND)
        return ResolveChatResult(chat_id=chat_id, kind="megagroup", member=True)

    def _participants(self, action: ReadChatParticipants) -> ReadChatParticipantsResult:
        if action.admins:
            return ReadChatParticipantsResult(users=self.admins.get(action.chat, []))
        everyone = self.members.get(action.chat, [])
        total = self.totals.get(action.chat, len(everyone))
        if action.chat in self.hidden and action.offset == 0:
            return ReadChatParticipantsResult(users=[], total=total, hidden=True)
        page = everyone[action.offset : action.offset + action.limit]
        return ReadChatParticipantsResult(users=page, total=total)

    def _history(self, action: ReadChatHistoryAuthors) -> ReadChatHistoryAuthorsResult:
        posts = _after(self.history.get(action.chat, []), action.offset_id)
        page = posts[: action.limit]
        done = len(page) < action.limit
        if action.min_date is not None:
            kept = [p for p in page if p.record().date >= action.min_date]
            done = done or len(kept) < len(page)
            page = kept
        return ReadChatHistoryAuthorsResult(
            messages=[p.record() for p in page], users=_authors(page), done=done
        )

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        self.writes.append((account_id, action))
        assert isinstance(action, JoinChannel)
        token = str(action.channel)
        status = self.join_status.get(token, "ok")
        if status in {"ok", "already_participant"}:
            self.inside.add((account_id, token))
        return ActionResult(
            status=status,  # ty: ignore[invalid-argument-type]
            action_type="join_channel",
            account_id=account_id,
            error_type="InviteRequestSentError" if status == "failed" else None,
            flood_wait_seconds=60 if status == "flood_wait" else None,
        )


def _after(posts: list[Post], offset_id: int) -> list[Post]:
    return [p for p in posts if offset_id == 0 or p.message_id < offset_id]


def _authors(posts: list[Post]) -> list[ParsedTelegramUser]:
    seen: dict[int, ParsedTelegramUser] = {}
    for post in posts:
        if post.author is not None:
            seen.setdefault(post.author.user_id, post.author)
    return list(seen.values())
