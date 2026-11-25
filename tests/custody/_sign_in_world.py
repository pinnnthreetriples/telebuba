"""The sign-in with the new API on the custody world (``submit_phone_code`` with ``new_key``).

The account's own file holds a key its status called gone (Telegram no longer honours it). The
sign-in re-authorizes it as a key made with the NEW API: from then on that key must be
connected with that API, so the device row has to say so, whatever happens in between.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.repositories.device_fingerprint import fetch_device_fingerprint
from core.telegram_client import _auth
from core.telegram_client._auth import submit_phone_code
from schemas.phone_login import PhoneCodeSubmit
from tests.custody._flows import (
    NEW_API_ID,
    assert_a_live_key_is_on_disk,
    assert_all_accounted,
    assert_no_silent_state,
    install_common,
    restart_and_repair,
    retry,
    seed_account,
)
from tests.custody._world import OLD, PHONE, World, read_key

if TYPE_CHECKING:
    import pytest

    from tests.custody._flows import Steps

ROLES = {"acc": "own"}
STEPS: Steps = [
    ("journal.open", True),
    ("own.build", False),
    ("own.connect", True),
    ("own.sign_in", True),
    ("gateway.record_api", True),
    ("own.get_me", False),
    ("own.disconnect", False),
    ("journal.close", True),
]


def install(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    install_common(world, monkeypatch)
    monkeypatch.setattr(_auth, "create_telegram_client", world.factory)
    real_record = _auth.record_key_api

    async def record_key_api(profile: object) -> None:
        await world.step("gateway.record_api", lambda: real_record(profile))  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(_auth, "record_key_api", record_key_api)
    monkeypatch.setattr(settings.telegram, "session_check_timeout_seconds", 5.0)


async def start(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await seed_account(world)
    # The key the file holds is gone: Telegram has revoked it.
    world.server.live.discard(OLD)
    install(world, monkeypatch)


async def sign_in_flow() -> object:
    return await submit_phone_code(
        PhoneCodeSubmit(
            account_id="acc", phone=PHONE, phone_code_hash="hash-1", code="54321", new_key=True
        )
    )


async def stored_api() -> int | None:
    device = await fetch_device_fingerprint("acc", "desktop")
    assert device is not None
    return device.api_id


async def check_after_repair(world: World) -> None:
    """I1-I3 for the sign-in: a live key is the account's file, with the API it was made with."""
    assert_a_live_key_is_on_disk(world, "after repair", none_live_ok=True)

    async def is_recorded(key: bytes) -> bool:
        return read_key(world.file("acc")) == key and await stored_api() == world.server.api_of[key]

    await assert_all_accounted(world, is_recorded)
    assert_no_silent_state(world)
    own = read_key(world.file("acc"))
    if own in world.server.live:
        made_with = world.server.api_of[own]
        assert await stored_api() == made_with, (
            f"I3: the key was signed in with API {made_with!r}, the row says {await stored_api()!r}"
        )


async def retry_after_repair(world: World) -> None:
    """The operator signs in again: it must not answer anything but alive."""
    world.phase = "flow"
    world.faults.clear()

    async def operation() -> None:
        result = await sign_in_flow()
        assert result.status == "alive", f"the retry answered {result.status!r}"  # ty: ignore[unresolved-attribute]

    await retry(operation, set())
    assert_a_live_key_is_on_disk(world, "after the retry")
    await restart_and_repair(world)
    await check_after_repair(world)
    assert world.server.api_of[OLD] == NEW_API_ID
