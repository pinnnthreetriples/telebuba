"""The second-device sign-in on the custody world: its seams, its steps, what must hold after."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account
from core.telegram_client import _second_device
from core.telegram_client._second_device import authorize_second_device
from schemas.devices import SecondDeviceSignIn
from tests.custody._flows import (
    assert_a_live_key_is_on_disk,
    assert_all_accounted,
    assert_no_silent_state,
    assert_the_account_works,
    install_common,
    open_or_incomplete,
    restart_and_repair,
    retry,
    seed_account,
)
from tests.custody._world import World, read_key

if TYPE_CHECKING:
    import pytest

    from tests.custody._flows import Steps

NAME = "acc_mobile"
ROLES = {NAME: "second"}
# The steps of a clean second-device sign-in, in order (a test pins the trace to this list).
STEPS: Steps = [
    ("primary.get_me", False),
    ("journal.open", True),
    ("second.build", False),
    ("second.connect", True),
    ("primary.read_cutoff", False),
    ("second.send_code", False),
    ("primary.wait_code", False),
    ("second.sign_in", True),
    ("journal.signed_in", True),
    ("second.get_me", False),
    ("db.record_device", "thread"),
    ("second.disconnect", False),
    ("journal.close", True),
]
# ``record_second_device`` runs as a shielded task: a cancellation reaches the flow only.
SHIELDED = ("db.record_device",)


def install(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    install_common(world, monkeypatch)
    monkeypatch.setattr(settings.telegram, "mobile_api_id", 6)
    monkeypatch.setattr(settings.telegram, "mobile_api_hash", "mobile-hash")

    async def get_client(account_id: str, device_type: str | None = None) -> object:
        assert (account_id, device_type) == ("acc", None), "the code is read on the primary"
        return world.pooled("primary", "acc")

    async def last_service_message_id(_primary: object) -> int:
        return await world.step("primary.read_cutoff", lambda: 1)

    async def wait_for_login_code(*_args: object, **_kwargs: object) -> str:
        return await world.step("primary.wait_code", lambda: "54321")

    real_record = _second_device.record_second_device

    async def record_second_device(*args: object, **kwargs: object) -> None:
        await world.step("db.record_device", lambda: real_record(*args, **kwargs))  # ty: ignore[invalid-argument-type]

    real_remove = _second_device._remove_session_file

    async def remove_session_file(session_path: str) -> None:
        await world.step("file.quarantine", lambda: real_remove(session_path))

    for attr, value in (
        ("get_client", get_client),
        ("create_telegram_client", world.factory),
        ("last_service_message_id", last_service_message_id),
        ("wait_for_login_code", wait_for_login_code),
        ("record_second_device", record_second_device),
        ("_remove_session_file", remove_session_file),
    ):
        monkeypatch.setattr(_second_device, attr, value)


async def start(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await seed_account(world)
    install(world, monkeypatch)


def _request() -> SecondDeviceSignIn:
    return SecondDeviceSignIn(account_id="acc", device_type="mobile", session_name=NAME)


async def second_flow() -> object:
    return await authorize_second_device(_request())


async def recorded(world: World, key: bytes) -> bool:
    """Whether a key a sign-in made is the account's second device: row and file agree."""
    account = await fetch_account("acc")
    return (
        account is not None
        and account.second_session_name == NAME
        and read_key(world.file(NAME)) == key
    )


async def check_after_repair(world: World) -> None:
    """I1-I3 for the second device, once the repair has run twice."""
    assert_a_live_key_is_on_disk(world, "after repair")
    await assert_the_account_works(world)

    async def is_recorded(key: bytes) -> bool:
        return await recorded(world, key)

    await assert_all_accounted(world, is_recorded)
    assert_no_silent_state(world)
    account = await fetch_account("acc")
    assert account is not None
    if account.second_session_name is not None:
        # The row says there is a second device: its file must hold a key Telegram honours.
        held = read_key(world.file(account.second_session_name))
        assert held in world.server.live, "I3: the recorded second device has no live key"


async def retry_after_repair(world: World) -> None:
    """The operator signs the second device in again: no leftover may be the reason it fails."""
    world.phase = "flow"
    world.faults.clear()

    async def operation() -> None:
        result = await second_flow()
        code = result.code  # ty: ignore[unresolved-attribute]
        allowed = {None, "device_already_present"}
        if open_or_incomplete():
            allowed.add("device_session_conflict")
        assert code in allowed, f"the retry answered {code!r}"

    await retry(operation, set())
    assert_a_live_key_is_on_disk(world, "after the retry")
    await restart_and_repair(world)
    await check_after_repair(world)
