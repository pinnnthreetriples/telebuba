"""What the engine makes of Telegram's refusals: floods, the spam block, closed DMs."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import masstagging as repository
from core.repositories.spam_status import get_spam_status
from core.telegram_client import UNCONFIRMED_ERROR_TYPE, TelegramReadError
from schemas.telegram_actions_masstagging import MasstagResolvedTarget, MasstagUser
from tests.services.masstagging.fakes import answer, run_to_end, seed

if TYPE_CHECKING:
    from tests.services.masstagging.fakes import Clock, FakeTelegram


async def _rows(campaign_id: str) -> dict[str, tuple[str, str | None]]:
    return {t.raw: (t.state, t.reason) for t in await repository.list_targets(campaign_id)}


async def _record(campaign_id: str) -> tuple[str, str | None]:
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    return record.run_status, record.last_error


@pytest.mark.asyncio
async def test_a_flood_pauses_the_account_and_the_people_wait(
    telegram: FakeTelegram, clock: Clock
) -> None:
    campaign_id = await seed(mode="priming", media=[], targets=("@alpha", "@beta"))
    telegram.toggle_answers["a1"] = [answer("flood_wait", flood_wait_seconds=30)]
    start = clock.now()

    await run_to_end(campaign_id)

    # The pause is never shorter than the campaign's floor (500 s), then both get done.
    assert {state for state, _reason in (await _rows(campaign_id)).values()} == {"tagged"}
    assert clock.now() - start >= 500
    journal = await repository.list_journal(campaign_id)
    assert any(e.event == "flood" and e.detail == "500" for e in journal)


@pytest.mark.asyncio
async def test_three_floods_in_a_row_stop_a_lone_account(telegram: FakeTelegram) -> None:
    campaign_id = await seed(mode="priming", media=[], targets=("@alpha",), floods_to_rest=3)
    telegram.toggle_answers["a1"] = [answer("flood_wait", flood_wait_seconds=10)] * 3

    await run_to_end(campaign_id)

    assert await _record(campaign_id) == ("failed", "floods")
    assert (await _rows(campaign_id))["@alpha"][0] == "flood"


@pytest.mark.asyncio
async def test_a_flooded_account_rests_while_another_goes_on(telegram: FakeTelegram) -> None:
    campaign_id = await seed(
        mode="priming",
        media=[],
        accounts=("a1", "a2"),
        targets=("@alpha", "@beta", "@gamma"),
        floods_to_rest=1,
    )
    telegram.toggle_answers["a1"] = [answer("flood_wait", flood_wait_seconds=10)]

    await run_to_end(campaign_id)

    assert {state for state, _reason in (await _rows(campaign_id)).values()} == {"tagged"}
    assert {account for account, _peer in telegram.toggles} == {"a2"}


@pytest.mark.asyncio
async def test_a_peer_flood_is_a_spam_block_and_the_account_leaves(telegram: FakeTelegram) -> None:
    campaign_id = await seed(mode="priming", media=[], targets=("@alpha", "@beta"))
    telegram.toggle_answers["a1"] = [answer("peer_flood")]

    await run_to_end(campaign_id)

    verdict = await get_spam_status("a1")
    assert verdict is not None
    assert verdict.status == "limited"
    assert await _record(campaign_id) == ("failed", "no_accounts")
    assert {state for state, _reason in (await _rows(campaign_id)).values()} == {"queued"}


@pytest.mark.asyncio
async def test_a_closed_dm_fails_only_that_person(telegram: FakeTelegram) -> None:
    campaign_id = await seed(mode="priming", media=[], targets=("@alpha", "@beta"))
    telegram.toggle_answers["a1"] = [answer("failed", error_type="UserPrivacyRestrictedError")]

    await run_to_end(campaign_id)

    rows = await _rows(campaign_id)
    assert rows["@alpha"] == ("failed", "privacy")
    assert rows["@beta"] == ("tagged", None)
    assert await _record(campaign_id) == ("done", None)


@pytest.mark.asyncio
async def test_a_closed_dm_keeps_the_person_out_of_the_story(telegram: FakeTelegram) -> None:
    campaign_id = await seed(targets=("@alpha", "@beta"), prime_first=True)
    telegram.toggle_answers["a1"] = [answer("failed", error_type="UserPrivacyRestrictedError")]

    await run_to_end(campaign_id)

    assert telegram.tagged() == ["beta"]


@pytest.mark.asyncio
async def test_names_nobody_has_and_strangers_phones_are_skipped(telegram: FakeTelegram) -> None:
    campaign_id = await seed(targets=("@ghost", "+79001234567", "@alpha"))
    telegram.lookups["ghost"] = MasstagResolvedTarget(outcome="not_found")

    await run_to_end(campaign_id)

    rows = await _rows(campaign_id)
    assert rows["@ghost"] == ("skipped", "username_not_found")
    assert rows["+79001234567"] == ("skipped", "phone_not_found")
    assert telegram.tagged() == ["alpha"]


@pytest.mark.asyncio
async def test_a_group_link_is_replaced_by_its_members(telegram: FakeTelegram) -> None:
    campaign_id = await seed(targets=("t.me/moscow_chat",))
    telegram.lookups["moscow_chat"] = MasstagResolvedTarget(
        outcome="group",
        members=[
            MasstagUser(user_id=1, username="alpha"),
            MasstagUser(user_id=2, first_name="Olga"),
            MasstagUser(user_id=3, username="robot", is_bot=True),
        ],
    )

    await run_to_end(campaign_id)

    rows = await _rows(campaign_id)
    assert rows["@moscow_chat"] == ("skipped", "chat_expanded")
    assert rows["@alpha"] == ("tagged", None)
    assert rows["Olga"] == ("tagged", None)
    assert "@robot" not in rows


@pytest.mark.asyncio
@pytest.mark.usefixtures("telegram")
async def test_someone_another_campaign_tagged_is_skipped() -> None:
    first = await seed(targets=("@alpha",))
    await run_to_end(first)
    second = await seed(accounts=("a2",), targets=("@alpha", "@beta"))

    await run_to_end(second)

    rows = await _rows(second)
    assert rows["@alpha"] == ("skipped", "already_tagged")
    assert rows["@beta"] == ("tagged", None)


@pytest.mark.asyncio
@pytest.mark.usefixtures("telegram")
async def test_the_registry_is_ignored_when_the_switch_is_off() -> None:
    first = await seed(targets=("@alpha",))
    await run_to_end(first)
    second = await seed(accounts=("a2",), targets=("@alpha",), skip_tagged=False)

    await run_to_end(second)

    assert (await _rows(second))["@alpha"] == ("tagged", None)


@pytest.mark.asyncio
async def test_a_lost_answer_to_a_story_is_never_posted_again(telegram: FakeTelegram) -> None:
    campaign_id = await seed(targets=("@alpha",))
    telegram.story_answers["a1"] = [answer("failed", error_type=UNCONFIRMED_ERROR_TYPE)]

    await run_to_end(campaign_id)

    assert (await _rows(campaign_id))["@alpha"] == ("failed", "unconfirmed")
    assert telegram.stories == []


@pytest.mark.asyncio
async def test_a_flood_on_the_lookup_pauses_the_account_too(telegram: FakeTelegram) -> None:
    campaign_id = await seed(targets=("@alpha",))
    telegram.lookup_errors["alpha"] = TelegramReadError("flood", kind="flood_wait", seconds=900)

    await run_to_end(campaign_id)

    journal = await repository.list_journal(campaign_id)
    assert any(e.event == "flood" and e.detail == "900" for e in journal)
    assert telegram.tagged() == ["alpha"]


@pytest.mark.asyncio
async def test_a_dead_session_takes_the_account_out(telegram: FakeTelegram) -> None:
    campaign_id = await seed(
        mode="priming", media=[], accounts=("a1", "a2"), targets=("@alpha", "@beta")
    )
    telegram.toggle_answers["a1"] = [answer("failed", error_message="session_dead")]

    await run_to_end(campaign_id)

    assert {state for state, _reason in (await _rows(campaign_id)).values()} == {"tagged"}
    assert {account for account, _peer in telegram.toggles} == {"a2"}
