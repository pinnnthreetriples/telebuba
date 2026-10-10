"""A run in each mode collects, dedups, filters and saves its people as a base."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import user_parser as repository
from core.telegram_client import TelegramReadError
from schemas.telegram_actions import ReadChatHistoryAuthors, ReadChatParticipants
from schemas.user_parser_run import UserParserRun, UserParserSourceReport, UserParserUser
from services.user_parser import _seams
from tests.services.user_parser.conftest import seed_accounts
from tests.services.user_parser.fakes import Post, person
from tests.services.user_parser.helpers import kept, parser_request, run_to_end

if TYPE_CHECKING:
    from pydantic import BaseModel

    from schemas.telegram_actions import TelegramReadAction
    from tests.services.user_parser.fakes import FakeTelegram

_ADMIN_REQUIRED = "RPC: ChatAdminRequiredError"


def _statuses(run: UserParserRun) -> list[tuple[str, str, int]]:
    return [(s.source, s.status, s.count) for s in run.sources]


@pytest.mark.asyncio
async def test_members_are_paged_deduped_across_sources_and_filtered(
    telegram: FakeTelegram,
) -> None:
    await seed_accounts("a1")
    telegram.members["one"] = [person(i) for i in range(1, 251)] + [person(900, is_bot=True)]
    telegram.members["two"] = [person(1), person(500)]

    run = await run_to_end(parser_request(sources=["@one", "t.me/two"]))

    assert run.status == "done"
    assert _statuses(run) == [("@one", "ok", 251), ("t.me/two", "ok", 2)]
    assert (run.collected_raw, run.kept, run.filtered) == (252, 251, {"bot": 1})
    assert (run.sources_done, run.sources_total) == (2, 2)
    pages = [a for _, a in telegram.reads if isinstance(a, ReadChatParticipants)]
    assert [(p.chat, p.offset) for p in pages] == [("one", 0), ("one", 200), ("two", 0)]
    one = next(u for u in await kept(run.run_id) if u.user_id == 1)
    assert one.sources == ["@one", "t.me/two"]


@pytest.mark.asyncio
async def test_the_member_cap_stops_the_walk_and_is_not_partial(telegram: FakeTelegram) -> None:
    await seed_accounts("a1")
    telegram.members["big"] = [person(i) for i in range(1, 400)]

    run = await run_to_end(parser_request(sources=["@big"], limits={"members": 250}))

    assert _statuses(run) == [("@big", "ok", 250)]
    pages = [a for _, a in telegram.reads if isinstance(a, ReadChatParticipants)]
    assert [(p.offset, p.limit) for p in pages] == [(0, 200), (200, 50)]


@pytest.mark.asyncio
async def test_fewer_members_than_telegram_claims_is_partial(telegram: FakeTelegram) -> None:
    await seed_accounts("a1")
    telegram.members["big"] = [person(i) for i in range(1, 11)]
    telegram.totals["big"] = 12000

    run = await run_to_end(parser_request(sources=["@big"]))

    (report,) = run.sources
    assert (report.status, report.count, report.total) == ("partial", 10, 12000)
    assert run.status == "done"


@pytest.mark.asyncio
async def test_a_hidden_member_list_says_so_and_the_run_goes_on(telegram: FakeTelegram) -> None:
    await seed_accounts("a1")
    telegram.hidden.add("secret")
    telegram.members["open"] = [person(1)]

    run = await run_to_end(parser_request(sources=["@secret", "@open"]))

    assert _statuses(run) == [("@secret", "hidden", 0), ("@open", "ok", 1)]
    assert run.kept == 1


@pytest.mark.asyncio
async def test_messages_count_only_what_the_toggles_and_keywords_let_through(
    telegram: FakeTelegram,
) -> None:
    await seed_accounts("a1")
    seller, chatter, forwarder = person(1), person(2), person(3)
    telegram.history["chat"] = [
        Post(9, seller, "Продаю SHOP", hours_ago=1),
        Post(8, seller, "ещё shop", hours_ago=2, is_reply=True),
        Post(7, chatter, "привет", hours_ago=3),
        Post(6, forwarder, "shop shop", hours_ago=4, is_forward=True),
        Post(5, None, "channel-signed", hours_ago=5),
        Post(4, seller, "старый shop", hours_ago=24 * 40),
    ]

    run = await run_to_end(
        parser_request(mode="messages", sources=["@chat"], keywords=["shop"], limits={"days": 30})
    )

    people = await kept(run.run_id)
    assert [(u.user_id, u.message_count) for u in people] == [(1, 2)]
    assert (run.collected_raw, run.filtered) == (3, {"min_messages": 2})
    (read,) = [a for _, a in telegram.reads if isinstance(a, ReadChatHistoryAuthors)]
    assert read.min_date is not None


@pytest.mark.asyncio
async def test_message_pages_follow_the_cursor_up_to_the_cap(telegram: FakeTelegram) -> None:
    await seed_accounts("a1")
    telegram.history["chat"] = [Post(i, person(i)) for i in range(300, 0, -1)]

    run = await run_to_end(
        parser_request(mode="messages", sources=["@chat"], limits={"messages": 150})
    )

    pages = [a for _, a in telegram.reads if isinstance(a, ReadChatHistoryAuthors)]
    assert [(p.offset_id, p.limit) for p in pages] == [(0, 100), (201, 50)]
    assert run.kept == 150


@pytest.mark.asyncio
async def test_a_topic_link_reads_that_forum_topic(telegram: FakeTelegram) -> None:
    await seed_accounts("a1")
    telegram.history["forum"] = [Post(1, person(1))]

    await run_to_end(parser_request(mode="messages", sources=["https://t.me/forum/123"]))

    (read,) = [a for _, a in telegram.reads if isinstance(a, ReadChatHistoryAuthors)]
    assert (read.chat, read.top_msg_id) == ("forum", 123)


@pytest.mark.asyncio
async def test_comments_walk_posts_then_their_threads(telegram: FakeTelegram) -> None:
    await seed_accounts("a1")
    telegram.history["news"] = [
        Post(30, None, replies=3),
        Post(29, None, replies=None),
        Post(28, None, replies=1),
        Post(27, None, replies=5),
    ]
    telegram.replies[("news", 30)] = [
        Post(103, person(1), "great post"),
        Post(102, person(2), "ok"),
        Post(101, person(1), "agree completely"),
    ]
    telegram.replies[("news", 28)] = [Post(201, person(3), "nice one")]

    run = await run_to_end(
        parser_request(
            mode="comments", sources=["@news"], limits={"posts": 3, "per_post": 2, "min_length": 3}
        )
    )

    people = await kept(run.run_id)
    # Post 27 is past the posts cap; post 30's thread stops at two comments; "ok" is too short.
    assert [(u.user_id, u.message_count) for u in people] == [(1, 1), (3, 1)]
    assert run.filtered == {"min_messages": 1}


@pytest.mark.asyncio
async def test_long_comment_threads_are_paged(telegram: FakeTelegram) -> None:
    await seed_accounts("a1")
    telegram.history["news"] = [Post(1, None, replies=250)]
    telegram.replies[("news", 1)] = [Post(i, person(i), "text") for i in range(1000, 750, -1)]

    run = await run_to_end(
        parser_request(mode="comments", sources=["@news"], limits={"per_post": 250})
    )

    assert run.kept == 250


@pytest.mark.asyncio
async def test_admins_and_our_own_accounts_are_left_out(telegram: FakeTelegram) -> None:
    await seed_accounts("a1", user_ids={"a1": 77})
    telegram.members["group"] = [person(1), person(2), person(77)]
    telegram.admins["group"] = [person(2)]

    run = await run_to_end(parser_request(toggles={"exclude_admins": True, "exclude_own": True}))

    assert [u.user_id for u in await kept(run.run_id)] == [1]
    assert run.filtered == {"own": 1, "admin": 1}
    admin_reads = [a for _, a in telegram.reads if isinstance(a, ReadChatParticipants) and a.admins]
    assert len(admin_reads) == 1


@pytest.mark.asyncio
async def test_a_failed_admin_read_does_not_fail_the_source(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broadcast channel lists its admins to admins only: that refusal is expected."""
    await seed_accounts("a1")
    telegram.members["news"] = [person(1)]

    async def _admins_refused(account_id: str, action: TelegramReadAction) -> BaseModel:
        if isinstance(action, ReadChatParticipants) and action.admins:
            raise TelegramReadError(_ADMIN_REQUIRED)
        return await telegram.execute_read(account_id, action)

    monkeypatch.setattr(_seams, "execute_read", _admins_refused)

    run = await run_to_end(parser_request(sources=["@news"], toggles={"exclude_admins": True}))

    assert _statuses(run) == [("@news", "ok", 1)]
    assert run.kept == 1


@pytest.mark.asyncio
async def test_people_from_earlier_bases_are_skipped_on_request(telegram: FakeTelegram) -> None:
    await seed_accounts("a1")
    earlier = UserParserRun(
        run_id="old", name="old", mode="members", status="running", created_at="2026-01-01"
    )
    await repository.create_run(earlier, parser_request())
    await repository.settle_run(
        earlier.model_copy(update={"status": "done"}), [UserParserUser(user_id=1)]
    )
    telegram.members["group"] = [person(1), person(2)]

    run = await run_to_end(
        parser_request(toggles={"exclude_collected": True, "exclude_admins": False})
    )

    assert [u.user_id for u in await kept(run.run_id)] == [2]
    assert run.filtered == {"collected": 1}


@pytest.mark.asyncio
async def test_the_intersection_keeps_people_found_in_enough_sources(
    telegram: FakeTelegram,
) -> None:
    await seed_accounts("a1")
    telegram.members["alpha"] = [person(1), person(2)]
    telegram.members["beta"] = [person(2), person(3)]

    run = await run_to_end(parser_request(sources=["@alpha", "@beta"], min_sources=2))

    assert [u.user_id for u in await kept(run.run_id)] == [2]


@pytest.mark.asyncio
async def test_an_unreadable_source_line_fails_alone(telegram: FakeTelegram) -> None:
    await seed_accounts("a1")
    telegram.members["fine"] = [person(1)]

    run = await run_to_end(parser_request(sources=["not a chat!", "@fine"]))

    assert _statuses(run) == [("not a chat!", "failed", 0), ("@fine", "ok", 1)]
    assert run.status == "done"
    assert isinstance(run.sources[0], UserParserSourceReport)
