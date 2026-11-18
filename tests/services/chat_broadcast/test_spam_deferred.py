"""A postponed, refused or failed @SpamBot check keeps the account resting; a block halts it."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from schemas.spam_status import SpamStatusVerdict
from services.chat_broadcast import _seams
from services.spam_intel import _gateway
from tests.services.chat_broadcast.fakes import refused, run_to_end, seed

if TYPE_CHECKING:
    from schemas.spam_intel import ServedFrom
    from tests.services.chat_broadcast.fakes import FakeTelegram


def _answer(status: str, served_from: ServedFrom | None) -> SpamStatusVerdict:
    return SpamStatusVerdict(
        account_id="a1",
        status=status,  # ty: ignore[invalid-argument-type]
        checked_at=datetime.now(UTC).isoformat(),
        served_from=served_from,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        (_answer("clean", "probe"), "clean"),
        (_answer("limited", "coalesced"), "limited"),
        (_answer("unknown", "probe"), "unknown"),
        (_answer("unknown", "deferred"), "deferred"),
        (_answer("unknown", "refused"), "refused"),
        (_answer("clean", None), "clean"),
    ],
)
async def test_the_seam_tells_a_postponed_check_from_a_failed_one(
    monkeypatch: pytest.MonkeyPatch, verdict: SpamStatusVerdict, expected: str
) -> None:
    seen: dict[str, object] = {}

    async def _ask(account_id: str, **kwargs: object) -> SpamStatusVerdict:
        seen.update(account_id=account_id, **kwargs)
        return verdict

    monkeypatch.setattr("services.spam_intel.ask", _ask)
    assert await _seams.spam_verdict("a1") == expected
    # Forced (a clean from yesterday says nothing about the refusal just got), by this domain.
    assert seen == {"account_id": "a1", "force": True, "holder": "chat_broadcast"}


@pytest.mark.asyncio
async def test_the_caller_scope_reaches_the_gateway() -> None:
    with _seams.spam_caller("chat_broadcast.rest_end"):
        assert _gateway._CALLER.get() == "chat_broadcast.rest_end"
    assert _gateway._CALLER.get() is None


@pytest.mark.asyncio
async def test_a_postponed_check_at_the_end_of_the_rest_keeps_resting_instead_of_halting(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha", "@beta"), messages=("Hi",))
    telegram.send_answers["a1"] = [refused("peer_flood")]
    script = iter(["deferred", "deferred", "clean"])
    callers: list[str | None] = []

    async def _verdict(account_id: str) -> str:
        telegram.spam_checks.append(account_id)
        callers.append(_gateway._CALLER.get())
        return next(script)

    monkeypatch.setattr(_seams, "spam_verdict", _verdict)

    await run_to_end(campaign_id)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
    assert len(telegram.sent) == 2
    assert telegram.spam_checks == ["a1", "a1", "a1"]
    assert set(callers) == {"chat_broadcast.rest_end"}
    roster = await repository.list_roster(campaign_id)
    assert (roster[0].state, roster[0].halted_reason) == ("active", None)


@pytest.mark.asyncio
async def test_a_transport_failure_inside_the_gateway_is_a_failed_check_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _ask(_account_id: str, **_kwargs: object) -> SpamStatusVerdict:
        raise TimeoutError

    monkeypatch.setattr("services.spam_intel.ask", _ask)
    assert await _seams.spam_verdict("a1") == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", ["unknown", "refused"])
async def test_a_failed_or_refused_check_at_the_end_of_the_rest_keeps_resting_never_halts(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch, failed: str
) -> None:
    """D9: a transport failure (or a refusal) is no reading, so it is never counted as a block."""
    campaign_id = await seed(accounts=("a1",), targets=("@alpha", "@beta"), messages=("Hi",))
    telegram.send_answers["a1"] = [refused("peer_flood")]
    script = iter([failed, failed, "clean"])

    async def _verdict(account_id: str) -> str:
        telegram.spam_checks.append(account_id)
        return next(script)

    monkeypatch.setattr(_seams, "spam_verdict", _verdict)

    await run_to_end(campaign_id)

    roster = await repository.list_roster(campaign_id)
    assert (roster[0].state, roster[0].halted_reason) == ("active", None)
    assert telegram.spam_checks == ["a1", "a1", "a1"]
    assert len(telegram.sent) == 2


@pytest.mark.asyncio
async def test_a_confirmed_block_at_the_end_of_the_rest_still_halts_the_account(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha", "@beta"), messages=("Hi",))
    telegram.send_answers["a1"] = [refused("peer_flood")]

    async def _verdict(account_id: str) -> str:
        telegram.spam_checks.append(account_id)
        return "limited"

    monkeypatch.setattr(_seams, "spam_verdict", _verdict)

    await run_to_end(campaign_id)

    roster = await repository.list_roster(campaign_id)
    assert (roster[0].state, roster[0].halted_reason) == ("halted", "spam_block")
    assert telegram.spam_checks == ["a1"]
