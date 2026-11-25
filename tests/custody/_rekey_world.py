"""The re-key flow on the custody world: its seams, its steps, what must hold after a crash."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.repositories.account_rekeys import fetch_rekey, save_rekey
from core.telegram_client import _rekey, _rekey_files
from services.accounts import _key_repair_rekey, session_rekey
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
from tests.custody._world import OLD, World, read_key

if TYPE_CHECKING:
    import pytest

    from tests.custody._flows import Steps

SCRATCH = "acc_rekey"
ROLES = {"acc": "check", SCRATCH: "new"}
# The steps of a clean re-key through the service, in order (a test pins the trace to this list).
# True: the step may have taken effect when its answer is lost (a worker thread runs on after
# a cancellation, a Telegram call may be applied and unanswered).
STEPS: Steps = [
    ("approver.list", False),
    ("approver.get_me", False),
    ("journal.open", True),
    ("new.build", False),
    ("new.connect", True),
    ("new.qr_login", False),
    ("approver.accept_token", True),
    ("journal.signed_in", True),
    ("new.get_me", False),
    ("new.list", False),
    ("journal.old_hash", True),
    ("new.disconnect", False),
    ("file.copy_aside", True),
    ("file.copy_final", True),
    ("check.build", False),
    ("check.connect", False),
    ("check.get_me", False),
    ("check.disconnect", False),
    ("file.unlink_aside", True),
    ("file.unlink_scratch", True),
    ("journal.swapped", True),
    ("gateway.record_api", True),
    ("svc.record_api", True),
    ("svc.store_row", True),
    ("svc.close_intent", True),
]


def install(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """Wire the re-key service to the fake server, naming each step the fault can hit."""
    install_common(world, monkeypatch)

    async def get_client(_account_id: str, _device: str | None = None) -> object:
        return world.pooled("approver", "acc")

    monkeypatch.setattr(_rekey, "get_client", get_client)
    monkeypatch.setattr(_rekey, "create_telegram_client", world.factory)

    real_open = _rekey.open_intent

    async def open_intent(**fields: object) -> int:
        return await world.step("journal.open", lambda: real_open(**fields))  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(_rekey, "open_intent", open_intent)

    real_copy = _rekey_files.copy_session

    def copy_session(source: str, target: str) -> None:
        if target.endswith(_rekey_files.ASIDE_SUFFIX):
            name = "file.copy_aside"
        elif source.endswith(_rekey_files.ASIDE_SUFFIX):
            name = "file.copy_back"
        else:
            name = "file.copy_final"
        world.sync_step(name, lambda: real_copy(source, target))

    real_unlink = _rekey_files.unlink

    async def unlink(path: str) -> None:
        name = (
            "file.unlink_aside"
            if path.endswith(_rekey_files.ASIDE_SUFFIX)
            else ("file.unlink_scratch")
        )
        await world.step(name, lambda: real_unlink(path))

    monkeypatch.setattr(_rekey_files, "copy_session", copy_session)
    monkeypatch.setattr(_rekey_files, "unlink", unlink)

    real_record = _rekey.record_key_api

    async def record_key_api(profile: object) -> None:
        await world.step("gateway.record_api", lambda: real_record(profile))  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(_rekey, "record_key_api", record_key_api)
    for attr, name in (
        ("set_device_api_id", "svc.record_api"),
        ("save_rekey", "svc.store_row"),
        ("close_intent", "svc.close_intent"),
    ):
        _wrap_service(world, monkeypatch, attr, name)

    async def no_audit(_account: object) -> None:
        return None

    monkeypatch.setattr(session_rekey, "refresh_account_audit", no_audit)
    monkeypatch.setattr(session_rekey, "log_event", world.log_stub())
    monkeypatch.setattr(settings.session_audit, "auto_close_old_key", False)


def _wrap_service(world: World, monkeypatch: pytest.MonkeyPatch, attr: str, name: str) -> None:
    real = getattr(_key_repair_rekey, attr)

    async def wrapper(*args: object, **kwargs: object) -> object:
        return await world.step(name, lambda: real(*args, **kwargs))

    monkeypatch.setattr(_key_repair_rekey, attr, wrapper)


async def start(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await seed_account(world)
    install(world, monkeypatch)


async def rekey_flow() -> object:
    return await session_rekey.rekey_account_session("acc")


async def recorded(world: World, key: bytes) -> bool:
    """Whether a key a re-key made is the account's own (the swap landed and was kept)."""
    return read_key(world.file("acc")) == key


async def check_after_repair(world: World) -> None:
    """I1-I3 for the re-key, once the repair has run twice."""
    assert_a_live_key_is_on_disk(world, "after repair")
    await assert_the_account_works(world)

    async def is_recorded(key: bytes) -> bool:
        return await recorded(world, key)

    await assert_all_accounted(world, is_recorded)
    assert_no_silent_state(world)
    # A file that holds another key than the one the re-key began with is a swap that landed:
    # the old authorization must be on record for the operator, or the intent still be open.
    if read_key(world.file("acc")) != OLD:
        row = await fetch_rekey("acc")
        assert row is not None or open_or_incomplete(), "I3: the swap landed and nobody knows"


async def retry_after_repair(world: World) -> None:
    """The operator ended the old authorization and retries: no leftover may be the reason.

    A re-key that is still waiting for its old key to be ended refuses a new one (honestly),
    which would hide a leftover that blocks the next; so the old key is ended first, as the
    operator does from the card. A conflict is only honest while an intent is still open.
    """
    row = await fetch_rekey("acc")
    if row is not None and row.closed_at is None:
        await save_rekey(row.model_copy(update={"closed_at": "2026-11-25T12:00:00+00:00"}))
    world.phase = "flow"
    world.faults.clear()
    refusals = {"rekey_needs_attention", "rekey_incomplete"}
    if open_or_incomplete():
        refusals.add("rekey_session_conflict")
    await retry(rekey_flow, refusals)
    assert_a_live_key_is_on_disk(world, "after the retry")
    await restart_and_repair(world)
    await assert_the_account_works(world)
