"""Inter-account dialogue pairing (#40).

Assigns each warming account a small set of partner accounts to converse with
and reshuffles the acquaintance graph on a configured interval, so the network
of "who knows whom" looks organic. The dialogue exchange itself builds on this.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    list_accounts,
    list_dialogue_pairs,
    list_recent_dialogue_messages,
    list_warming_states,
    replace_dialogue_pairs,
)
from core.logging import log_event
from schemas.accounts import health_for_status
from schemas.dialogues import (
    DialogueFeed,
    DialogueFeedMessage,
    DialoguePairsResult,
    DialoguePartnersResult,
)
from schemas.warming import is_warming

if TYPE_CHECKING:
    from schemas.dialogues import DialoguePair

_rng = random.SystemRandom()
_MIN_POOL = 2


async def get_partners(account_id: str) -> DialoguePartnersResult:
    """Return the accounts paired with ``account_id`` (either side of a pair)."""
    partners: list[str] = []
    for pair in await list_dialogue_pairs():
        if pair.account_a == account_id:
            partners.append(pair.account_b)
        elif pair.account_b == account_id:
            partners.append(pair.account_a)
    return DialoguePartnersResult(partners=partners)


async def _eligible_accounts() -> list[str]:
    records = await list_warming_states()
    warming_ids = {record.account_id for record in records if is_warming(record.state)}
    return sorted(
        account.account_id
        for account in (await list_accounts()).accounts
        if account.account_id in warming_ids and health_for_status(account.status) != "fail"
    )


def _needs_reshuffle(pairs: list[DialoguePair], eligible: list[str], now: datetime) -> bool:
    if not pairs:
        return True
    covered = {pair.account_a for pair in pairs} | {pair.account_b for pair in pairs}
    if covered != set(eligible):
        return True
    try:
        newest = max(datetime.fromisoformat(pair.assigned_at) for pair in pairs)
    except ValueError:
        return True
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=UTC)
    return now - newest >= timedelta(days=settings.warming.dialogue_reshuffle_days)


def _build_pairs(accounts: list[str]) -> list[tuple[str, str]]:
    warm = settings.warming
    shuffled = list(accounts)
    _rng.shuffle(shuffled)
    pairs: set[tuple[str, str]] = set()
    for account in shuffled:
        others = [other for other in shuffled if other != account]
        if not others:
            continue
        upper = min(warm.dialogue_partners_max, len(others))
        lower = min(warm.dialogue_partners_min, upper)
        for partner in _rng.sample(others, _rng.randint(lower, upper)):
            ordered = sorted((account, partner))
            pairs.add((ordered[0], ordered[1]))
    return sorted(pairs)


async def assign_pairs(*, force: bool = False) -> DialoguePairsResult:
    """Reshuffle the acquaintance graph when stale, membership-changed, or forced.

    A pool below two eligible accounts clears any existing pairs (nobody to talk
    to). Otherwise pairs are rebuilt only when needed, to avoid churn.
    """
    eligible = await _eligible_accounts()
    pairs = await list_dialogue_pairs()
    now = datetime.now(UTC)

    if len(eligible) < _MIN_POOL:
        if pairs:
            await replace_dialogue_pairs([])
        return DialoguePairsResult(pairs=[])

    if not force and not _needs_reshuffle(pairs, eligible, now):
        return DialoguePairsResult(pairs=pairs)

    new_pairs = _build_pairs(eligible)
    await replace_dialogue_pairs(new_pairs)
    await log_event(
        "INFO",
        "dialogue_pairs_assigned",
        extra={"accounts": len(eligible), "pairs": len(new_pairs)},
    )
    return DialoguePairsResult(pairs=await list_dialogue_pairs())


async def load_dialogue_overview(*, recent_limit: int = 30) -> DialogueFeed:
    """Recent inter-account messages with both sides resolved to a display label.

    The label is the account's phone (fallback: label, fallback: bare id) so the
    feed stays locale-neutral (#12). Accounts are read in a single pass and
    indexed, so resolving both ends of every message is O(1), never N+1.
    """
    messages = await list_recent_dialogue_messages(recent_limit)
    labels = {
        account.account_id: account.phone or account.label or account.account_id
        for account in (await list_accounts()).accounts
    }

    def _label(account_id: str) -> str:
        return labels.get(account_id, account_id)

    return DialogueFeed(
        messages=[
            DialogueFeedMessage(
                from_account=message.from_account,
                from_label=_label(message.from_account),
                to_account=message.to_account,
                to_label=_label(message.to_account),
                text=message.text,
                created_at=message.created_at,
                replied=message.replied,
            )
            for message in messages
        ],
    )
