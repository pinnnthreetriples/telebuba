"""Who to tag: parsing what the operator typed, a list file, a parser base, the contacts.

A target is a username (``@ivan``, ``ivan``, ``t.me/ivan``) or a phone (``+7…``). A
``t.me/`` link to a group looks exactly like one to a person, so both are usernames
here; the run finds out which it is and puts a group's members in its place. Invite,
folder and post links name no person and are refused.

The key is what makes two spellings one target: ``u:<username lowercased>``,
``p:<digits>``, or ``id:<user id>`` for a contact only its own account can reach.
"""

from __future__ import annotations

import csv
import io
import re
from typing import TYPE_CHECKING

from core.config import settings
from core.repositories import user_parser as user_parser_repository
from schemas.masstagging import MasstaggingTargetList
from schemas.masstagging_records import TargetSeed
from services.masstagging._errors import (
    BASE_NOT_FOUND,
    IMPORT_INVALID,
    INVALID_TARGET,
    TOO_MANY_TARGETS,
    MasstaggingInvalidError,
)

if TYPE_CHECKING:
    from schemas.telegram_actions_masstagging import MasstagUser

_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_LINK = re.compile(r"^(?:https?://)?(?:www\.)?(?:t|telegram)\.me/(.+)$", re.IGNORECASE)
_PHONE_CHARS = re.compile(r"[\s\-()]")
_MIN_PHONE, _MAX_PHONE = 7, 15
_SPLIT = re.compile(r"[\s,;]+")
_USERNAME_HEADER = "username"
_PAGE = 1000


def parse_target(raw: str) -> TargetSeed | None:
    """One typed target, or ``None`` when it is neither a username nor a phone."""
    text = raw.strip()
    if text.startswith("+"):
        digits = _PHONE_CHARS.sub("", text[1:])
        if digits.isdigit() and _MIN_PHONE <= len(digits) <= _MAX_PHONE:
            return TargetSeed(target_key=f"p:{digits}", raw=f"+{digits}", kind="phone")
        return None
    if (link := _LINK.match(text)) is not None:
        text = link.group(1).split("?", 1)[0].rstrip("/")
        if "/" in text:
            return None
    name = text.removeprefix("@")
    if _USERNAME.fullmatch(name) is None:
        return None
    return TargetSeed(target_key=f"u:{name.lower()}", raw=f"@{name}", kind="username")


def split_targets(text: str) -> list[str]:
    return [item for item in _SPLIT.split(text) if item]


def seeds_of(targets: list[str]) -> list[TargetSeed]:
    """The saved list as targets, one per key, in order; invalid entries left out."""
    seeds: dict[str, TargetSeed] = {}
    for raw in targets:
        seed = parse_target(raw)
        if seed is not None:
            seeds.setdefault(seed.target_key, seed)
    return list(seeds.values())


def valid_targets(targets: list[str]) -> list[str]:
    """One entry per person, in the order given; refuses what no run could use."""
    seeds: dict[str, str] = {}
    for raw in targets:
        seed = parse_target(raw)
        if seed is None:
            raise MasstaggingInvalidError(INVALID_TARGET)
        seeds.setdefault(seed.target_key, seed.raw)
    if len(seeds) > settings.masstagging.max_targets_per_campaign:
        raise MasstaggingInvalidError(TOO_MANY_TARGETS)
    return list(seeds.values())


def _tokens(text: str) -> list[str]:
    """A CSV with a ``username`` column gives that column; anything else is split."""
    lines = text.splitlines()
    if lines:
        header = next(csv.reader([lines[0]]), [])
        columns = [cell.strip().lower() for cell in header]
        if _USERNAME_HEADER in columns:
            index = columns.index(_USERNAME_HEADER)
            rows = csv.reader(io.StringIO("\n".join(lines[1:])))
            return [row[index] for row in rows if len(row) > index and row[index].strip()]
    return split_targets(text)


def import_file(content: bytes) -> MasstaggingTargetList:
    """Targets from a text or CSV file; what is not a target is counted, not refused."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MasstaggingInvalidError(IMPORT_INVALID) from exc
    tokens = _tokens(text)
    targets = [seed.raw for seed in seeds_of(tokens)]
    skipped = sum(1 for token in tokens if parse_target(token) is None)
    limit = settings.masstagging.max_targets_per_campaign
    return MasstaggingTargetList(targets=targets[:limit], skipped=skipped)


async def from_base(base_id: str) -> MasstaggingTargetList:
    """A saved parser base as usernames; people without one, bots and deleted ones skip.

    Copied, never linked: the campaign keeps what the base held when it was picked.
    """
    run = await user_parser_repository.fetch_run(base_id)
    if run is None:
        raise MasstaggingInvalidError(BASE_NOT_FOUND)
    limit = settings.masstagging.max_targets_per_campaign
    targets: list[str] = []
    skipped = 0
    after: int | None = None
    while len(targets) < limit:
        users = await user_parser_repository.users_after(base_id, after, _PAGE)
        for user in users:
            if user.username and not user.is_bot and not user.is_deleted:
                targets.append(f"@{user.username}")
            else:
                skipped += 1
        if len(users) < _PAGE:
            break
        after = users[-1].user_id
    return MasstaggingTargetList(targets=targets[:limit], skipped=skipped, name=run.name)


def contact_seed(user: MasstagUser, account_id: str) -> TargetSeed:
    """A contact of ``account_id``: reachable by id from that account only."""
    raw = f"@{user.username}" if user.username else (user.first_name or str(user.user_id))
    return TargetSeed(
        target_key=f"id:{user.user_id}",
        raw=raw,
        kind="contact",
        assigned_account_id=account_id,
        resolved_user_id=user.user_id,
        username=user.username,
        first_name=user.first_name,
    )


def member_seed(user: MasstagUser, account_id: str) -> TargetSeed:
    """A member of a group link, found by ``account_id``.

    With a username anyone may tag them (and the key matches a typed ``@name``);
    without one only the account that saw them can.
    """
    if user.username:
        return TargetSeed(
            target_key=f"u:{user.username.lower()}",
            raw=f"@{user.username}",
            kind="link",
            username=user.username,
            first_name=user.first_name,
        )
    seed = contact_seed(user, account_id)
    return seed.model_copy(update={"kind": "link"})
