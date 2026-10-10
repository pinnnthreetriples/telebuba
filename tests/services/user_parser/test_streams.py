"""Floods, failures and pauses: one account's trouble never stops the run on its own."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.telegram_client import TelegramReadError
from schemas.telegram_actions import ReadChatParticipants
from services.user_parser import _seams
from services.user_parser._run import pause_seconds
from tests.services.user_parser.conftest import seed_accounts
from tests.services.user_parser.fakes import NOW, person
from tests.services.user_parser.helpers import parser_request, run_to_end

if TYPE_CHECKING:
    from pydantic import BaseModel

    from schemas.telegram_actions import TelegramReadAction
    from tests.services.user_parser.conftest import Seams
    from tests.services.user_parser.fakes import FakeTelegram

_PAGE = "read_chat_participants"
_PRIVATE = "RPC: ChannelPrivateError"


def _flood(seconds: int | None) -> TelegramReadError:
    return TelegramReadError(f"FloodWait({seconds}s)", kind="flood_wait", seconds=seconds)


def _readers(telegram: FakeTelegram) -> list[str]:
    return [account for account, a in telegram.reads if isinstance(a, ReadChatParticipants)]


@pytest.mark.asyncio
async def test_a_short_flood_is_sat_out_and_the_page_read_again(
    telegram: FakeTelegram, seams: Seams
) -> None:
    await seed_accounts("a1")
    telegram.members["group"] = [person(1), person(2)]
    telegram.fail(_flood(30), action_type=_PAGE)

    run = await run_to_end(parser_request())

    assert (run.status, run.kept) == ("done", 2)
    assert 30 in seams.sleeps
    assert seams.cooldowns == {"a1": NOW + timedelta(seconds=30)}
    assert _readers(telegram) == ["a1", "a1"]
    assert [(s.status, s.count) for s in run.sources] == [("ok", 2)]


@pytest.mark.asyncio
async def test_a_long_flood_drops_the_account_and_its_page_goes_to_another(
    telegram: FakeTelegram, seams: Seams
) -> None:
    await seed_accounts("a1", "a2")
    telegram.members["group"] = [person(1)]
    telegram.fail(_flood(3600), action_type=_PAGE, account_id="a1")

    run = await run_to_end(parser_request(account_ids=["a1", "a2"]))

    assert (run.status, run.kept) == ("done", 1)
    assert _readers(telegram) == ["a1", "a2"]
    assert 3600 not in seams.sleeps
    assert "a1" in seams.cooldowns


@pytest.mark.asyncio
async def test_a_page_is_retried_once_only(telegram: FakeTelegram) -> None:
    await seed_accounts("a1", "a2", "a3")
    telegram.members["group"] = [person(1)]
    telegram.fail(_flood(3600), action_type=_PAGE, times=2)

    run = await run_to_end(parser_request(account_ids=["a1", "a2", "a3"]))

    # a1 flooded, its one retry on a2 flooded too: the page is given up, a3 never asked.
    assert _readers(telegram) == ["a1", "a2"]
    assert (run.status, [(s.status, s.count) for s in run.sources]) == ("done", [("flood", 0)])


@pytest.mark.asyncio
async def test_with_every_account_flooded_the_run_ends_and_keeps_what_it_has(
    telegram: FakeTelegram,
) -> None:
    await seed_accounts("a1")
    telegram.members["first"] = [person(1)]
    telegram.members["second"] = [person(2)]
    telegram.fail(_flood(None), action_type=_PAGE, times=1)

    run = await run_to_end(
        parser_request(sources=["@first", "@second"], toggles={"exclude_admins": False})
    )

    # Peer flood: no duration, so the configured cooldown — far past the sit-out ceiling.
    assert (
        settings.user_parser.peer_flood_cooldown_seconds
        > settings.user_parser.flood_sit_out_max_seconds
    )
    assert (run.status, run.stop_reason) == ("failed", "flooded")
    assert [s.status for s in run.sources] == ["flood", "flood"]


@pytest.mark.asyncio
async def test_failed_reads_in_a_row_take_the_account_out(telegram: FakeTelegram) -> None:
    await seed_accounts("a1")
    for name in ("one", "two", "three", "four"):
        telegram.members[name] = [person(len(name))]
    telegram.fail(TelegramReadError(_PRIVATE), action_type=_PAGE, times=3)

    run = await run_to_end(parser_request(sources=["@one", "@two", "@three", "@four"]))

    assert (run.status, run.stop_reason) == ("failed", "aborted")
    assert [s.status for s in run.sources] == ["failed", "failed", "failed", "failed"]
    assert len(_readers(telegram)) == 3


@pytest.mark.asyncio
async def test_a_success_resets_the_error_streak(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_accounts("a1")
    names = ("one", "two", "three", "four", "five")
    for index, name in enumerate(names):
        telegram.members[name] = [person(index + 1)]
    calls = {"n": 0}

    async def _every_other(account_id: str, action: TelegramReadAction) -> BaseModel:
        calls["n"] += 1
        if calls["n"] % 2:
            raise TelegramReadError(_PRIVATE)
        return await telegram.execute_read(account_id, action)

    monkeypatch.setattr(_seams, "execute_read", _every_other)
    run = await run_to_end(parser_request(sources=[f"@{n}" for n in names]))

    assert run.status == "done"
    assert [s.status for s in run.sources] == ["failed", "ok", "failed", "ok", "failed"]


@pytest.mark.asyncio
async def test_an_account_that_never_connects_leaves_at_once(telegram: FakeTelegram) -> None:
    await seed_accounts("a1", "a2")
    telegram.members["group"] = [person(1)]
    telegram.fail(
        TelegramReadError("unavailable: ConnectionError", kind="unavailable"),
        action_type=_PAGE,
        account_id="a1",
    )

    run = await run_to_end(parser_request(account_ids=["a1", "a2"]))

    assert (run.status, run.kept) == ("done", 1)
    assert _readers(telegram) == ["a1", "a2"]


@pytest.mark.asyncio
async def test_an_account_parked_mid_run_hands_its_page_back(
    telegram: FakeTelegram, seams: Seams, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_accounts("a1", "a2")
    telegram.members["first"] = [person(1)]
    telegram.members["second"] = [person(2)]
    telegram.members["third"] = [person(3)]

    async def _park_after_first(account_id: str, action: TelegramReadAction) -> BaseModel:
        result = await telegram.execute_read(account_id, action)
        seams.cooling.add("a1")
        return result

    monkeypatch.setattr(_seams, "execute_read", _park_after_first)
    run = await run_to_end(
        parser_request(sources=["@first", "@second", "@third"], account_ids=["a1", "a2"])
    )

    assert (run.status, run.kept) == ("done", 3)
    assert _readers(telegram).count("a1") == 1


def _ctx(**overrides: object) -> object:
    from types import SimpleNamespace  # noqa: PLC0415

    return SimpleNamespace(request=parser_request(**overrides))


@pytest.mark.parametrize(
    ("overrides", "new_source", "expected"),
    [
        ({"chat_delay": 6, "request_delay": 2}, True, 6),
        ({"chat_delay": 6, "request_delay": 2}, False, 2),
        ({"chat_delay": 6, "request_delay": 2, "fast": True}, True, 3),
        ({"chat_delay": 6, "request_delay": 2, "fast": True}, False, 1),
        # "Fast" never goes below the configured floors.
        ({"chat_delay": 1, "request_delay": 0.5, "fast": True}, True, 1.0),
        ({"chat_delay": 1, "request_delay": 0.5, "fast": True}, False, 0.5),
    ],
)
def test_pauses_per_source_and_per_page(
    overrides: dict[str, object], *, new_source: bool, expected: float
) -> None:
    ctx = _ctx(**overrides)
    assert pause_seconds(ctx, new_source=new_source) == pytest.approx(expected)  # ty: ignore[invalid-argument-type]


def test_protect_jitters_within_the_configured_share(monkeypatch: pytest.MonkeyPatch) -> None:
    jitter = settings.user_parser.jitter
    monkeypatch.setattr(_seams.rng, "uniform", lambda low, _high: low)
    low = pause_seconds(_ctx(chat_delay=10, protect=True), new_source=True)  # ty: ignore[invalid-argument-type]
    monkeypatch.setattr(_seams.rng, "uniform", lambda _low, high: high)
    high = pause_seconds(_ctx(chat_delay=10, protect=True), new_source=True)  # ty: ignore[invalid-argument-type]

    assert (low, high) == (pytest.approx(10 * (1 - jitter)), pytest.approx(10 * (1 + jitter)))


@pytest.mark.asyncio
async def test_the_run_paces_between_sources_and_pages(
    telegram: FakeTelegram, seams: Seams
) -> None:
    await seed_accounts("a1")
    telegram.members["first"] = [person(i) for i in range(1, 301)]
    telegram.members["second"] = [person(500)]

    await run_to_end(parser_request(sources=["@first", "@second"], chat_delay=7, request_delay=3))

    # Page two of the first source, then the second source: no pause before the first read.
    assert seams.sleeps == [3, 7]
