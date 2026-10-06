"""Which chats a run writes to, and which account takes each.

"By list": links in order, dealt round-robin over the accounts; a folder link opens into
its groups. "Where it already is": every group the accounts can write to, a shared group
going to ONE of them — the one with the fewest chats so far. One chat, one account.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.telegram_client import TelegramAccountNotFoundError, TelegramReadError
from schemas.chat_broadcast_board import (
    ChatBroadcastOwnChats,
    ChatBroadcastOwnGroup,
    ChatBroadcastResolved,
    ChatBroadcastResolvedTarget,
)
from schemas.chat_broadcast_records import TargetSeed
from schemas.telegram_actions import CheckChatlist, ListWritableGroups
from schemas.telegram_actions_broadcast import ChatlistResult, WritableGroupsResult
from services.chat_broadcast import _seams
from services.chat_broadcast.links import (
    folder_chat_key,
    own_chat_key,
    parse_target,
    split_targets,
)

if TYPE_CHECKING:
    from schemas.chat_broadcast import ChatBroadcastResolveRequest, ChatBroadcastSettings

logger = logging.getLogger(__name__)
_READ_ERRORS = (TelegramReadError, TelegramAccountNotFoundError)


async def _writable_groups(account_id: str) -> WritableGroupsResult | None:
    try:
        result = await _seams.execute_read(
            account_id, ListWritableGroups(limit=settings.chat_broadcast.dialogs_scan_limit)
        )
    except _READ_ERRORS:
        logger.warning("could not list the groups of %s", account_id, exc_info=True)
        return None
    return result if isinstance(result, WritableGroupsResult) else None


async def _chatlist(account_id: str, slug: str) -> ChatlistResult | None:
    try:
        result = await _seams.execute_read(account_id, CheckChatlist(slug=slug))
    except _READ_ERRORS:
        logger.warning("could not open folder %s with %s", slug, account_id, exc_info=True)
        return None
    return result if isinstance(result, ChatlistResult) else None


async def own_chats(account_ids: list[str]) -> ChatBroadcastOwnChats:
    """The groups the accounts are in — what "where it already is" will write to."""
    groups: dict[int, ChatBroadcastOwnGroup] = {}
    channels = admin_only = 0
    unavailable: list[str] = []
    for account_id in dict.fromkeys(account_ids):
        listed = await _writable_groups(account_id)
        if listed is None:
            unavailable.append(account_id)
            continue
        channels += listed.channels_skipped
        admin_only += listed.admin_only_skipped
        for group in listed.groups:
            entry = groups.setdefault(
                group.peer_id,
                ChatBroadcastOwnGroup(
                    peer_id=str(group.peer_id),
                    title=group.title,
                    username=group.username,
                    account_ids=[],
                ),
            )
            entry.account_ids.append(account_id)
    return ChatBroadcastOwnChats(
        groups=list(groups.values()),
        channels_skipped=channels,
        admin_only_skipped=admin_only,
        unavailable_account_ids=unavailable,
    )


async def resolve(request: ChatBroadcastResolveRequest) -> ChatBroadcastResolved:
    """Classify pasted links for the settings dialog; folders are opened to count them."""
    items: list[ChatBroadcastResolvedTarget] = []
    reader = request.account_ids[0] if request.account_ids else None
    for raw in split_targets(request.targets):
        parsed = parse_target(raw, max_length=settings.chat_broadcast.max_target_length)
        if parsed is None:
            items.append(ChatBroadcastResolvedTarget(raw=raw, error="invalid_target"))
            continue
        item = ChatBroadcastResolvedTarget(raw=raw, key=parsed.key, kind=parsed.kind)
        if parsed.kind == "folder":
            folder = None if reader is None else await _chatlist(reader, parsed.token)
            if folder is None:
                item.error = "folder_unavailable"
            else:
                item.folder_title = folder.title or None
                item.folder_count = len(folder.groups)
        items.append(item)
    return ChatBroadcastResolved(items=items)


async def materialize(
    campaign_id: str, value: ChatBroadcastSettings, accounts: list[str]
) -> list[TargetSeed]:
    """The run's chats with an account each; empty when there is nothing to write to."""
    if value.target_mode == "own":
        return await _own_seeds(value, accounts)
    return await _list_seeds(campaign_id, value, accounts)


async def _list_seeds(
    campaign_id: str, value: ChatBroadcastSettings, accounts: list[str]
) -> list[TargetSeed]:
    seeds: dict[str, TargetSeed] = {}
    usernames: set[str] = set()
    for raw in split_targets(value.targets):
        parsed = parse_target(raw, max_length=settings.chat_broadcast.max_target_length)
        if parsed is None:
            continue
        if parsed.kind != "folder":
            username = parsed.token if parsed.kind == "public" else None
            if username:
                usernames.add(username.lower())
            seeds.setdefault(
                parsed.key,
                TargetSeed(chat_key=parsed.key, raw=raw, kind=parsed.kind, username=username),
            )
            continue
        folder = await _chatlist(accounts[0], parsed.token) if accounts else None
        if folder is None:
            await log_event(
                "WARNING",
                "chat_broadcast_folder_unavailable",
                extra={"campaign_id": campaign_id, "chat": raw},
            )
            continue
        for group in folder.groups:
            if group.username and group.username.lower() in usernames:
                continue
            key = folder_chat_key(parsed.token, group.peer_id)
            seeds.setdefault(
                key,
                TargetSeed(
                    chat_key=key,
                    raw=raw,
                    kind="folder",
                    title=group.title,
                    username=group.username,
                    folder_slug=parsed.token,
                    peer_id=group.peer_id,
                ),
            )
    ordered = list(seeds.values())
    for index, seed in enumerate(ordered):
        seed.assigned_account_id = accounts[index % len(accounts)] if accounts else None
    return ordered


async def _own_seeds(value: ChatBroadcastSettings, accounts: list[str]) -> list[TargetSeed]:
    listed = await own_chats(accounts)
    excluded = set(value.own_excluded)
    load: Counter[str] = Counter()
    seeds: list[TargetSeed] = []
    for group in listed.groups:
        if group.peer_id in excluded:
            continue
        account_id = min(group.account_ids, key=lambda a: (load[a], accounts.index(a)))
        load[account_id] += 1
        peer_id = int(group.peer_id)
        seeds.append(
            TargetSeed(
                chat_key=own_chat_key(peer_id),
                raw=f"@{group.username}" if group.username else group.title,
                kind="own",
                title=group.title,
                username=group.username,
                peer_id=peer_id,
                assigned_account_id=account_id,
                member_account_id=account_id,
            )
        )
    return seeds
