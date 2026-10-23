"""Inter-account dialogue pairing (#40).

Assigns each warming account a small set of partner accounts to converse with
and reshuffles the acquaintance graph on a configured interval, so the network
of "who knows whom" looks organic. The dialogue exchange itself builds on this.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    list_accounts,
    list_dialogue_pairs,
    list_recent_dialogue_messages,
    list_warming_states,
    prune_and_add_pairs,
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
from schemas.warming import is_warming, spam_block_known
from services._account_language import account_language

if TYPE_CHECKING:
    from collections.abc import Mapping

    from schemas.dialogues import DialoguePair

_rng = random.SystemRandom()
_MIN_POOL = 2
# Serialize the read-modify-write below: concurrent start_warming calls hold
# different per-account locks, so without this they could interleave
# replace_dialogue_pairs and churn assigned_at.
_assign_lock = asyncio.Lock()


async def get_partners(account_id: str) -> DialoguePartnersResult:
    """Return the accounts paired with ``account_id`` (either side of a pair)."""
    partners: list[str] = []
    for pair in await list_dialogue_pairs():
        if pair.account_a == account_id:
            partners.append(pair.account_b)
        elif pair.account_b == account_id:
            partners.append(pair.account_a)
    return DialoguePartnersResult(partners=partners)


async def _eligible_accounts() -> dict[str, str | None]:
    """Warming accounts that can talk, sorted, each with its phone's language.

    An account with a confirmed spam block cannot write to strangers, so it is never
    anyone's partner: the other side would wait on a reply that cannot come. Nor is a
    phone stint: its visits happen in the app, which never writes a DM.
    """
    warming_ids = {
        record.account_id
        for record in await list_warming_states()
        if is_warming(record.state)
        and not spam_block_known(record)
        and record.device_mode != "mobile"
    }
    eligible = {
        account.account_id: account_language(account.phone)
        for account in (await list_accounts()).accounts
        if account.account_id in warming_ids and health_for_status(account.status) != "fail"
    }
    return dict(sorted(eligible.items()))


def _speaks_alike(languages: Mapping[str, str | None] | None, a: str, b: str) -> bool:
    """Chat pairs only with a common language (4.2); no map = no restriction."""
    return languages is None or languages.get(a) == languages.get(b)


def _has_foreign_pair(pairs: list[DialoguePair], languages: Mapping[str, str | None]) -> bool:
    """A pair from before language pairing, whose two sides speak differently."""
    return any(
        pair.account_a in languages
        and pair.account_b in languages
        and not _speaks_alike(languages, pair.account_a, pair.account_b)
        for pair in pairs
    )


def _time_to_reshuffle(pairs: list[DialoguePair], now: datetime) -> bool:
    """True once the graph's OLDEST pair is older than the reshuffle interval.

    Keyed off the oldest ``assigned_at`` (not the newest) so incremental
    membership patches — which stamp only their new pairs — never keep pushing
    the organic full-reshuffle clock back. Unparseable timestamps force a rebuild.
    """
    try:
        oldest = min(datetime.fromisoformat(pair.assigned_at) for pair in pairs)
    except ValueError:
        return True
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=UTC)
    return now - oldest >= timedelta(days=settings.warming.dialogue_reshuffle_days)


def _pairs_for_new_accounts(
    new_accounts: list[str],
    eligible: list[str],
    languages: Mapping[str, str | None] | None = None,
) -> list[tuple[str, str]]:
    """Weave each newly-eligible account into the graph, leaving the rest alone.

    Mirrors :func:`_build_pairs`' per-account degree (``randint(min, max)``) but
    only for the new accounts, so existing acquaintances — and their live
    conversations — survive a membership change. Pairs are canonical (a < b).
    """
    warm = settings.warming
    pairs: set[tuple[str, str]] = set()
    for account in new_accounts:
        others = [
            other
            for other in eligible
            if other != account and _speaks_alike(languages, account, other)
        ]
        if not others:
            continue
        upper = min(warm.dialogue_partners_max, len(others))
        lower = min(warm.dialogue_partners_min, upper)
        for partner in _rng.sample(others, _rng.randint(lower, upper)):
            ordered = sorted((account, partner))
            pairs.add((ordered[0], ordered[1]))
    return sorted(pairs)


def _build_pairs(
    accounts: list[str],
    languages: Mapping[str, str | None] | None = None,
) -> list[tuple[str, str]]:
    warm = settings.warming
    shuffled = list(accounts)
    _rng.shuffle(shuffled)
    pairs: set[tuple[str, str]] = set()
    for account in shuffled:
        others = [
            other
            for other in shuffled
            if other != account and _speaks_alike(languages, account, other)
        ]
        if not others:
            continue
        upper = min(warm.dialogue_partners_max, len(others))
        lower = min(warm.dialogue_partners_min, upper)
        for partner in _rng.sample(others, _rng.randint(lower, upper)):
            ordered = sorted((account, partner))
            pairs.add((ordered[0], ordered[1]))
    return sorted(pairs)


async def assign_pairs(*, force: bool = False) -> DialoguePairsResult:
    """Keep the acquaintance graph current: full reshuffle on the interval, else patch.

    A pool below two eligible accounts clears any existing pairs (nobody to talk
    to). A full ``_build_pairs`` reshuffle runs only on first assignment, when
    forced, or once the interval elapses — the intended organic re-mixing. In
    between, a membership change is applied as a *targeted patch* (drop pairs of
    accounts that fell out, weave in new ones) so a single account freezing never
    reshuffles the whole graph and orphans every live conversation.
    """
    async with _assign_lock:
        languages = await _eligible_accounts()
        eligible = list(languages)
        pairs = await list_dialogue_pairs()
        now = datetime.now(UTC)

        if len(eligible) < _MIN_POOL:
            if pairs:
                await replace_dialogue_pairs([])
            return DialoguePairsResult(pairs=[])

        # A cross-language pair (from before language pairing) forces a rebuild too.
        reshuffle = not pairs or _time_to_reshuffle(pairs, now)
        if force or reshuffle or _has_foreign_pair(pairs, languages):
            new_pairs = _build_pairs(eligible, languages)
            await replace_dialogue_pairs(new_pairs)
            await log_event(
                "INFO",
                "warming_dialogue_pairs_assigned",
                extra={"accounts": len(eligible), "pairs": len(new_pairs)},
            )
            return DialoguePairsResult(pairs=await list_dialogue_pairs())

        eligible_set = set(eligible)
        covered = {pair.account_a for pair in pairs} | {pair.account_b for pair in pairs}
        removed = covered - eligible_set
        added = sorted(eligible_set - covered)
        if not removed and not added:
            return DialoguePairsResult(pairs=pairs)

        new_pairs = _pairs_for_new_accounts(added, eligible, languages) if added else []
        await prune_and_add_pairs(removed, new_pairs)
        await log_event(
            "INFO",
            "warming_dialogue_pairs_patched",
            extra={"removed": len(removed), "added": len(added), "new_pairs": len(new_pairs)},
        )
        return DialoguePairsResult(pairs=await list_dialogue_pairs())


async def load_dialogue_overview(*, recent_limit: int = 30) -> DialogueFeed:
    """Recent inter-account messages with both sides resolved for display.

    Each side carries a label — the account's phone (fallback: label, fallback:
    bare id) — plus its Telegram name parts, so the SPA can show the name and
    fall back to the label on its own. Both stay locale-neutral (#12). Accounts
    are read in a single pass and indexed, so resolving both ends of every
    message is O(1), never N+1.
    """
    messages = await list_recent_dialogue_messages(recent_limit)
    accounts = {account.account_id: account for account in (await list_accounts()).accounts}

    def _identity(account_id: str) -> tuple[str, str | None, str | None]:
        """``(label, first_name, last_name)`` for one side of a message."""
        account = accounts.get(account_id)
        if account is None:
            return account_id, None, None
        return account.phone or account.label or account_id, account.first_name, account.last_name

    feed: list[DialogueFeedMessage] = []
    for message in messages:
        from_label, from_first, from_last = _identity(message.from_account)
        to_label, to_first, to_last = _identity(message.to_account)
        feed.append(
            DialogueFeedMessage(
                from_account=message.from_account,
                from_label=from_label,
                from_first_name=from_first,
                from_last_name=from_last,
                to_account=message.to_account,
                to_label=to_label,
                to_first_name=to_first,
                to_last_name=to_last,
                text=message.text,
                created_at=message.created_at,
                replied=message.replied,
            ),
        )
    return DialogueFeed(messages=feed)
