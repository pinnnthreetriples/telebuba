"""Starting a run: every account checked, all reserved or none, a refusal names one."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.db import create_account
from core.repositories import user_parser as repository
from schemas.accounts import AccountCreate
from services import _account_owner
from services.user_parser import (
    UserParserInvalidError,
    _pool,
    _seams,
    _state,
    get_run,
    list_reading_accounts,
    start_run,
)
from services.warming import _exclusion
from tests.services.user_parser.conftest import seed_accounts
from tests.services.user_parser.fakes import person
from tests.services.user_parser.helpers import parser_request, run_to_end

if TYPE_CHECKING:
    from pydantic import BaseModel

    from schemas.telegram_actions import TelegramReadAction
    from tests.services.user_parser.conftest import Seams
    from tests.services.user_parser.fakes import FakeTelegram


@pytest.mark.asyncio
async def test_a_run_holds_its_accounts_and_gives_them_back(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_accounts("a1", "a2")
    telegram.members["group"] = [person(1)]
    gate = asyncio.Event()

    async def _slow(account_id: str, action: TelegramReadAction) -> BaseModel:
        await gate.wait()
        return await telegram.execute_read(account_id, action)

    monkeypatch.setattr(_seams, "execute_read", _slow)

    outcome = await start_run(parser_request(account_ids=["a2", "a1"]))
    assert outcome.status == "started"
    assert _account_owner.owners() == {"a1": "user_parser", "a2": "user_parser"}
    # Warming now refuses the account, naming who holds it.
    with pytest.raises(_exclusion.AccountUnavailableError) as refused:
        _exclusion.assert_not_campaign_held("a1")
    assert refused.value.code == "account_busy_user_parser"

    live = await get_run(outcome.run_id or "")
    assert live is not None
    assert (live.status, [a.account_id for a in live.accounts]) == ("running", ["a2", "a1"])

    again = await start_run(parser_request(account_ids=["a1"]))
    assert (again.status, again.refused_account_id) == ("already_running", "a1")

    gate.set()
    entry = _state.live(outcome.run_id or "")
    assert entry is not None
    assert entry.task is not None
    await entry.task
    assert _account_owner.owners() == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("holder", "status"),
    [("neuroshilling", "account_busy"), ("chat_broadcast", "account_busy")],
)
async def test_an_account_another_feature_holds_is_refused_by_name(
    telegram: FakeTelegram,  # noqa: ARG001 - patches the seams
    holder: _account_owner.Owner,
    status: str,
) -> None:
    await seed_accounts("a1", "a2")
    _account_owner.try_claim("a2", holder, "campaign")

    outcome = await start_run(parser_request(account_ids=["a1", "a2"]))

    assert (outcome.status, outcome.refused_account_id) == (status, "a2")
    # All or nothing: the free account was not taken either.
    assert _account_owner.owner_of("a1") is None
    assert (await repository.list_bases()).items == []


@pytest.mark.asyncio
async def test_unknown_sessionless_and_cooling_accounts_are_refused(
    telegram: FakeTelegram,  # noqa: ARG001 - patches the seams
    seams: Seams,
) -> None:
    await seed_accounts("a1")
    await create_account(AccountCreate(account_id="bare", label="bare"))
    seams.cooling.add("a1")

    missing = await start_run(parser_request(account_ids=["ghost"]))
    bare = await start_run(parser_request(account_ids=["bare"]))
    cooling = await start_run(parser_request(account_ids=["a1"]))

    assert (missing.status, missing.refused_account_id) == ("no_account", "ghost")
    assert (bare.status, bare.refused_account_id) == ("no_account", "bare")
    assert (cooling.status, cooling.refused_account_id) == ("account_cooling", "a1")


@pytest.mark.asyncio
async def test_a_holder_appearing_after_the_check_is_caught_under_the_lock(
    telegram: FakeTelegram,  # noqa: ARG001 - patches the seams
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_accounts("a1", "a2")
    original = _pool.check_accounts

    async def _then_taken(account_ids: list[str]) -> object:
        names = await original(account_ids)
        _account_owner.try_claim("a2", "chat_broadcast", "late")
        return names

    monkeypatch.setattr("services.user_parser.runs.check_accounts", _then_taken)

    outcome = await start_run(parser_request(account_ids=["a1", "a2"]))

    assert (outcome.status, outcome.refused_account_id) == ("account_busy", "a2")
    assert _account_owner.owner_of("a1") is None


@pytest.mark.asyncio
async def test_a_start_past_the_ceilings_is_invalid(
    telegram: FakeTelegram,  # noqa: ARG001 - patches the seams
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import settings  # noqa: PLC0415

    monkeypatch.setattr(settings.user_parser, "max_sources_per_run", 1)
    monkeypatch.setattr(settings.user_parser, "max_accounts_per_run", 1)

    with pytest.raises(UserParserInvalidError) as sources:
        await start_run(parser_request(sources=["@one", "@two"]))
    with pytest.raises(UserParserInvalidError) as accounts:
        await start_run(parser_request(account_ids=["a1", "a2"]))

    assert sources.value.code == "user_parser_too_many_sources"
    assert accounts.value.code == "user_parser_too_many_accounts"


@pytest.mark.asyncio
async def test_the_picker_marks_busy_and_cooling_accounts_premium_first(
    telegram: FakeTelegram,  # noqa: ARG001 - patches the seams
    seams: Seams,
) -> None:
    await seed_accounts("zed", "amy", "cool")
    await create_account(AccountCreate(account_id="bare", label="bare"))
    _account_owner.try_claim("amy", "neuroshilling", "c")
    seams.cooling.add("cool")

    items = (await list_reading_accounts()).items

    reasons = {item.account_id: item.busy_reason for item in items}
    assert reasons == {
        "zed": None,
        "amy": "account_busy",
        "cool": "account_cooling",
        "bare": "no_session",
    }
    assert [item.name for item in items] == sorted(item.name for item in items)


@pytest.mark.asyncio
async def test_the_name_the_modal_sent_is_kept(telegram: FakeTelegram) -> None:
    await seed_accounts("a1")
    telegram.members["group"] = [person(1)]

    named = await run_to_end(parser_request(name="  Крипта · 09.10  "))
    unnamed = await run_to_end(parser_request())

    assert named.name == "Крипта · 09.10"
    assert unnamed.name == "members · 2026-10-09"
