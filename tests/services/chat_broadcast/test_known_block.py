"""A spam block the account is already known to carry rests it; it is not discovered by sending.

Design 5.12 / owner decision D14's sibling for the broadcast: the stored status, read through
``spam_intel.decision_status``, rests a ``limited`` account through the rest a PeerFlood takes
(``spam_rest_seconds``, reason ``peer_flood``) BEFORE it joins or writes anything. The rest end
keeps its semantics: a postponed answer keeps resting, a block still standing takes the account
out, an unknown status never rests it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import create_account, upsert_spam_status
from core.repositories import chat_broadcast as repository
from schemas.accounts import AccountCreate
from schemas.spam_status import SpamStatusVerdict
from services.chat_broadcast import _seams
from tests.services.chat_broadcast.fakes import run_to_end, seed

if TYPE_CHECKING:
    from collections.abc import Callable

    from schemas.spam_status import SpamStatusKind
    from tests.services.chat_broadcast.fakes import Clock, FakeTelegram


async def _store(account_id: str, status: SpamStatusKind, detail: str | None = None) -> None:
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id=account_id,
            status=status,
            detail=detail,
            checked_at=datetime.now(UTC).isoformat(),
        )
    )


def _answers(
    monkeypatch: pytest.MonkeyPatch,
    telegram: FakeTelegram,
    *script: str,
    on_check: Callable[[], None] | None = None,
) -> None:
    """@SpamBot answers from ``script`` (the last one repeats) and a real answer is STORED.

    The gateway persists every reading it takes, which is what keeps an account that came back
    clean from being rested again by its own old ``limited`` row.
    """
    queue = list(script)

    async def _verdict(account_id: str) -> str:
        telegram.spam_checks.append(account_id)
        if on_check is not None:
            on_check()
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if answer in {"clean", "limited"}:  # a failed, refused or postponed ask stores nothing
            await _store(account_id, answer)  # ty: ignore[invalid-argument-type]
        return answer

    monkeypatch.setattr(_seams, "spam_verdict", _verdict)


async def _run(campaign_id: str) -> None:
    """Run to the end, but never hang the suite on a rest loop."""
    await asyncio.wait_for(run_to_end(campaign_id), timeout=30)


@pytest.mark.asyncio
async def test_a_known_block_rests_the_account_before_it_joins_or_writes_anything(
    telegram: FakeTelegram, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha", "@beta"), messages=("Hi",))
    await _store("a1", "limited")
    started = clock.now()
    at_the_check: list[tuple[int, int, int]] = []
    _answers(
        monkeypatch,
        telegram,
        "clean",
        on_check=lambda: at_the_check.append(
            (len(telegram.joins), len(telegram.sent), clock.now() - started)
        ),
    )

    await _run(campaign_id)

    # Nothing was joined or sent by the time the rest ended and @SpamBot was asked; the rest was
    # the PeerFlood one (spam_rest_seconds plus the margin).
    (joins, sent, waited) = at_the_check[0]
    assert (joins, sent) == (0, 0)
    assert waited >= int(settings.chat_broadcast.spam_rest_seconds)
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
    assert len(telegram.sent) == 2  # and then it worked, @SpamBot having said clean


@pytest.mark.asyncio
async def test_the_rest_is_the_peer_flood_rest(
    telegram: FakeTelegram, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), messages=("Hi",))
    await _store("a1", "limited")
    _answers(monkeypatch, telegram, "limited")  # the check at the end still finds the block
    rests: list[tuple[str, int]] = []
    original = repository.rest_account

    async def _spy(campaign: str, account: str, until: int, reason: str) -> None:
        rests.append((reason, until - clock.now()))
        await original(campaign, account, until, reason)

    monkeypatch.setattr(repository, "rest_account", _spy)

    await _run(campaign_id)

    reason, seconds = rests[0]
    assert reason == "peer_flood"
    assert seconds == int(
        settings.chat_broadcast.spam_rest_seconds + settings.chat_broadcast.rest_margin_seconds
    )


@pytest.mark.asyncio
async def test_a_block_still_standing_at_the_end_of_the_rest_takes_the_account_out(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha", "@beta"), messages=("Hi",))
    await _store("a1", "limited")
    _answers(monkeypatch, telegram, "limited")

    await _run(campaign_id)

    roster = await repository.list_roster(campaign_id)
    assert (roster[0].state, roster[0].halted_reason) == ("halted", "spam_block")
    assert telegram.sent == []
    assert telegram.joins == []


@pytest.mark.asyncio
async def test_a_postponed_check_at_the_end_keeps_the_known_block_resting(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), messages=("Hi",))
    await _store("a1", "limited")
    _answers(monkeypatch, telegram, "deferred", "deferred", "clean")

    await _run(campaign_id)

    roster = await repository.list_roster(campaign_id)
    assert (roster[0].state, roster[0].halted_reason) == ("active", None)
    assert telegram.spam_checks == ["a1", "a1", "a1"]
    assert len(telegram.sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("no_answer", ["unknown", "refused"])
async def test_a_failed_or_refused_check_at_the_end_keeps_the_known_block_resting(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch, no_answer: str
) -> None:
    """D9: a transport failure or a refusal is no reading, so it never takes the account out."""
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), messages=("Hi",))
    await _store("a1", "limited")
    _answers(monkeypatch, telegram, no_answer, no_answer, "clean")

    await _run(campaign_id)

    roster = await repository.list_roster(campaign_id)
    assert (roster[0].state, roster[0].halted_reason) == ("active", None)
    assert telegram.spam_checks == ["a1", "a1", "a1"]
    assert len(telegram.sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [None, "clean", "unknown"])
async def test_an_account_not_known_to_be_blocked_is_not_rested(
    telegram: FakeTelegram, stored: str | None
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha", "@beta"), messages=("Hi",))
    if stored is not None:
        await _store("a1", stored)  # ty: ignore[invalid-argument-type]

    await _run(campaign_id)

    assert telegram.spam_checks == []  # no rest, so no check at its end
    assert len(telegram.sent) == 2


@pytest.mark.asyncio
async def test_a_blocked_account_leaves_its_chats_to_one_that_is_not(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(accounts=("a1", "a2"), targets=("@alpha", "@beta"), messages=("Hi",))
    await _store("a1", "limited")
    await _store("a2", "clean")

    await _run(campaign_id)

    assert {sent.account_id for sent in telegram.sent} == {"a2"}
    assert len(telegram.sent) == 2


@pytest.mark.asyncio
async def test_the_seam_reads_the_stored_status_without_asking_telegram(
    telegram: FakeTelegram,
) -> None:
    assert await _seams.known_spam_status("nobody") == "unknown"
    await create_account(AccountCreate(account_id="a1"))
    await _store("a1", "limited")
    assert await _seams.known_spam_status("a1") == "limited"
    assert telegram.spam_checks == []


@pytest.mark.asyncio
async def test_a_dated_limit_that_ended_reads_unknown_only_when_decisions_are_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The readers' own rule (``decision_status``): the fold decides with passive decisions on."""
    ended = (datetime.now(UTC) - timedelta(days=30)).strftime("%d %b %Y")
    await create_account(AccountCreate(account_id="a1"))
    await _store("a1", "limited", f"Your account is limited until {ended}.")

    monkeypatch.setattr(settings.spam_intel, "passive_decisions", False)
    assert await _seams.known_spam_status("a1") == "limited"
    monkeypatch.setattr(settings.spam_intel, "passive_decisions", True)
    assert await _seams.known_spam_status("a1") == "unknown"
