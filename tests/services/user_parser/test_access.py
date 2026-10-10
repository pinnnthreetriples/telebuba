"""Private invite chats: joined automatically, but only when the account is not inside yet."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from core.repositories.neurocomment import count_account_joins_since, record_join
from core.telegram_client import TelegramReadError
from schemas.telegram_actions import JoinChannel, ReadChatParticipants, ResolveChat
from services.user_parser import _seams
from tests.services.user_parser.conftest import seed_accounts
from tests.services.user_parser.fakes import person
from tests.services.user_parser.helpers import parser_request, run_to_end

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult, TelegramAction
    from tests.services.user_parser.fakes import FakeTelegram

_INVITE = "https://t.me/+AbCdEfGh1234"
_TOKEN = "+AbCdEfGh1234"
_SINCE = datetime(2000, 1, 1, tzinfo=UTC).isoformat()


def _joins(telegram: FakeTelegram) -> list[str]:
    return [account for account, a in telegram.writes if isinstance(a, JoinChannel)]


@pytest.mark.asyncio
async def test_an_account_already_inside_reads_without_joining(telegram: FakeTelegram) -> None:
    await seed_accounts("a1")
    telegram.invites[_TOKEN] = 555
    telegram.inside.add(("a1", _TOKEN))
    telegram.members["555"] = [person(1)]

    run = await run_to_end(parser_request(sources=[_INVITE]))

    assert _joins(telegram) == []
    assert [(s.status, s.count) for s in run.sources] == [("ok", 1)]
    page = next(a for _, a in telegram.reads if isinstance(a, ReadChatParticipants))
    assert page.chat == "555"
    assert await count_account_joins_since("a1", _SINCE) == 0


@pytest.mark.asyncio
async def test_an_account_outside_joins_once_and_is_charged_for_it(telegram: FakeTelegram) -> None:
    await seed_accounts("a1")
    telegram.invites[_TOKEN] = 555
    telegram.members["555"] = [person(i) for i in range(1, 251)]

    run = await run_to_end(parser_request(sources=[_INVITE]))

    assert _joins(telegram) == ["a1"]
    assert run.kept == 250
    # Asked before joining, and once more to learn the chat id; then cached for page two.
    resolves = [a for _, a in telegram.reads if isinstance(a, ResolveChat)]
    assert len(resolves) == 2
    assert await count_account_joins_since("a1", _SINCE) == 1


@pytest.mark.asyncio
async def test_a_chat_that_cannot_be_joined_is_reported_and_the_run_goes_on(
    telegram: FakeTelegram,
) -> None:
    """A join request (the fake answers ``InviteRequestSentError``) is not membership."""
    await seed_accounts("a1")
    telegram.invites[_TOKEN] = 555
    telegram.join_status[_TOKEN] = "failed"
    telegram.members["open"] = [person(1)]

    run = await run_to_end(parser_request(sources=[_INVITE, "@open"]))

    assert [(s.source, s.status) for s in run.sources] == [
        (_INVITE, "join_failed"),
        ("@open", "ok"),
    ]
    assert run.status == "done"


@pytest.mark.asyncio
async def test_the_days_join_cap_is_respected(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.config import settings  # noqa: PLC0415

    monkeypatch.setattr(settings.user_parser, "max_joins_per_account_per_day", 1)
    await seed_accounts("a1")
    await record_join("a1")
    telegram.invites[_TOKEN] = 555

    run = await run_to_end(parser_request(sources=[_INVITE]))

    assert _joins(telegram) == []
    assert run.sources[0].status == "join_failed"


@pytest.mark.asyncio
async def test_a_join_flood_hands_the_chat_to_another_account(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_accounts("a1", "a2")
    telegram.invites[_TOKEN] = 555
    telegram.members["555"] = [person(1)]
    original = telegram.execute

    async def _flood_first(account_id: str, action: TelegramAction) -> ActionResult:
        if account_id == "a1":
            telegram.join_status[_TOKEN] = "flood_wait"
            result = await original(account_id, action)
            telegram.join_status[_TOKEN] = "ok"
            return result
        return await original(account_id, action)

    monkeypatch.setattr(_seams, "execute", _flood_first)
    run = await run_to_end(parser_request(sources=[_INVITE], account_ids=["a1", "a2"]))

    # 60 s sits under the ceiling, so a1 waits it out while a2 takes the retried page.
    assert sorted(_joins(telegram)) == ["a1", "a2"]
    assert (run.status, run.kept) == ("done", 1)


@pytest.mark.asyncio
async def test_a_flood_while_asking_membership_is_retried(telegram: FakeTelegram) -> None:
    await seed_accounts("a1")
    telegram.invites[_TOKEN] = 555
    telegram.inside.add(("a1", _TOKEN))
    telegram.members["555"] = [person(1)]
    telegram.fail(
        TelegramReadError("FloodWait(5s)", kind="flood_wait", seconds=5), action_type="resolve_chat"
    )

    run = await run_to_end(parser_request(sources=[_INVITE]))

    assert (run.status, run.kept) == ("done", 1)
    assert _joins(telegram) == []


@pytest.mark.asyncio
async def test_a_capped_account_hands_the_chat_to_one_with_room(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The day's cap belongs to the account, not to the chat."""
    from core.config import settings  # noqa: PLC0415

    monkeypatch.setattr(settings.user_parser, "max_joins_per_account_per_day", 1)
    await seed_accounts("a1", "a2")
    await record_join("a1")
    telegram.invites[_TOKEN] = 555
    telegram.members["555"] = [person(1)]

    run = await run_to_end(parser_request(sources=[_INVITE], account_ids=["a1", "a2"]))

    assert _joins(telegram) == ["a2"]
    assert (run.sources[0].status, run.kept) == ("ok", 1)
