"""The sign-in journal: opened only for a new key with an API the row lacks, closed always."""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from core.db import _get_engine, create_account, insert_device_fingerprint
from core.device_fingerprint import generate_random_device_fingerprint
from core.repositories.device_fingerprint import set_device_api_id
from core.telegram_client import _key_intent
from core.telegram_client._key_intent import sign_in_intent
from schemas.accounts import AccountCreate
from schemas.device_fingerprint import TelegramClientProfile
from schemas.key_intent import KeyIntent


def _profile(api_id: int | None) -> TelegramClientProfile:
    device = generate_random_device_fingerprint("acc").model_copy(update={"api_id": api_id})
    return TelegramClientProfile(
        account_id="acc",
        device_type="desktop",
        session_path="/sessions/acc",
        receive_updates=False,
        device=device,
    )


def _read_all() -> list[KeyIntent]:
    with _get_engine().connect() as connection:
        rows = connection.exec_driver_sql("select * from account_key_intents").mappings().all()
    return [KeyIntent.model_validate(dict(row)) for row in rows]


async def _intents() -> list[KeyIntent]:
    return await asyncio.to_thread(_read_all)


@pytest_asyncio.fixture(autouse=True)
async def _account() -> None:
    await create_account(AccountCreate(account_id="acc", session_name="acc"))
    await insert_device_fingerprint(generate_random_device_fingerprint("acc"))


@pytest.mark.asyncio
async def test_a_new_key_with_a_new_api_is_journaled_and_closed_once_its_api_is_recorded() -> None:
    async with sign_in_intent(_profile(7), new_key=True):
        (open_one,) = await _intents()
        assert (open_one.kind, open_one.api_id, open_one.session_name) == ("sign_in", 7, "acc")
        assert open_one.closed_at is None
        await set_device_api_id("acc", "desktop", 7)  # what the flow does after the sign-in

    (closed,) = await _intents()
    assert (closed.outcome, closed.closed_at is not None) == ("done", True)


@pytest.mark.asyncio
async def test_a_sign_in_that_never_recorded_its_api_leaves_the_intent_for_the_repair() -> None:
    """Ended cleanly or not: a key made with an API nobody stored is the repair's to settle."""
    async with sign_in_intent(_profile(7), new_key=True):
        pass

    (intent,) = await _intents()
    assert (intent.outcome, intent.closed_at) == (None, None)


@pytest.mark.asyncio
async def test_a_failing_sign_in_leaves_the_intent_open() -> None:
    with pytest.raises(RuntimeError):
        async with sign_in_intent(_profile(7), new_key=True):
            raise RuntimeError

    (intent,) = await _intents()
    assert (intent.outcome, intent.closed_at) == (None, None)


@pytest.mark.asyncio
async def test_a_cancelled_sign_in_leaves_the_intent_open() -> None:
    entered = asyncio.Event()

    async def _flow() -> None:
        async with sign_in_intent(_profile(7), new_key=True):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(_flow())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    (intent,) = await _intents()
    assert (intent.outcome, intent.closed_at) == (None, None)


@pytest.mark.asyncio
async def test_a_cancelled_sign_in_that_had_recorded_its_api_is_still_closed() -> None:
    recorded = asyncio.Event()

    async def _flow() -> None:
        async with sign_in_intent(_profile(7), new_key=True):
            await set_device_api_id("acc", "desktop", 7)
            recorded.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(_flow())
    await recorded.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    (intent,) = await _intents()
    assert intent.outcome == "done"


@pytest.mark.asyncio
async def test_nothing_is_journaled_for_an_old_key_the_default_api_or_a_known_api() -> None:
    async with sign_in_intent(_profile(7), new_key=False):
        pass
    async with sign_in_intent(_profile(None), new_key=True):
        pass
    await set_device_api_id("acc", "desktop", 7)
    async with sign_in_intent(_profile(7), new_key=True):
        pass

    assert await _intents() == []


@pytest.mark.asyncio
async def test_a_journal_that_cannot_be_written_does_not_stop_the_sign_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _broken(**_: object) -> int:
        raise OSError

    monkeypatch.setattr(_key_intent, "open_intent", _broken)
    ran = False

    async with sign_in_intent(_profile(7), new_key=True):
        ran = True

    assert ran
