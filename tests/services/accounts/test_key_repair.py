"""Repairing interrupted key flows from the journal and the real session files."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from core.config import settings
from core.db import create_account, fetch_account, insert_device_fingerprint
from core.device_fingerprint import generate_random_device_fingerprint
from core.device_fingerprint_android import generate_android_fingerprint
from core.repositories.account_key_intents import fetch_intent, open_intent
from core.repositories.account_rekeys import fetch_rekey, save_rekey
from core.repositories.device_fingerprint import (
    fetch_device_fingerprint,
    record_second_device,
    set_device_api_id,
)
from core.telegram_client import account_auth_lock
from schemas.accounts import AccountCreate
from schemas.session_rekey import StoredRekey
from services.accounts import _key_repair, _key_repair_files, _key_repair_rekey
from services.accounts._key_repair import repair_key_intents
from services.accounts._result import AccountActionError
from tests.session_files import write_session_file

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from schemas.key_intent import KeyIntentKind, KeyIntentStage

_OLD = b"old-key-bytes"
_NEW = b"new-key-bytes"


def _mark(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()


def _sessions() -> Path:
    return settings.telegram.session_dir


class _World:
    """Events logged, and what the self-logout of a leftover file was asked and answered."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []
        self.ended = True
        self.end_calls: list[tuple[str, str, str, object]] = []


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[_World]:
    state = _World()

    async def _log(
        _level: str, event: str, account_id: str | None = None, extra: dict | None = None
    ) -> None:
        state.events.append((event, {"account_id": account_id, **(extra or {})}))

    async def _end(
        account_id: str, device_type: str, session_name: str, *, device: object = None
    ) -> bool:
        state.end_calls.append((account_id, device_type, session_name, device))
        if state.ended:
            (_sessions() / f"{session_name}.session").unlink(missing_ok=True)
        return state.ended

    for module in (_key_repair, _key_repair_files, _key_repair_rekey):
        monkeypatch.setattr(module, "log_event", _log, raising=False)
    monkeypatch.setattr(_key_repair, "end_leftover_session", _end)
    monkeypatch.setattr(_key_repair_rekey, "end_leftover_session", _end)
    _key_repair.reset_for_tests()
    yield state
    _key_repair.reset_for_tests()


@pytest_asyncio.fixture(autouse=True)
async def _account(world: _World) -> None:  # noqa: ARG001 - the world is patched first
    await create_account(AccountCreate(account_id="acc", session_name="acc"))
    await insert_device_fingerprint(generate_random_device_fingerprint("acc"))


async def _rekey_intent(
    *,
    stage: KeyIntentStage = "signed_in",
    old_hash: str | None = "111",
    mark: bytes | None = _OLD,
    api_id: int | None = None,
) -> int:
    from core.repositories.account_key_intents import advance_intent  # noqa: PLC0415

    intent_id = await open_intent(
        account_id="acc",
        kind="rekey",
        device_type="desktop",
        session_name="acc_rekey",
        api_id=api_id,
        old_key_mark=None if mark is None else _mark(mark),
    )
    await advance_intent(intent_id, stage=stage, new_hash="222", old_hash=old_hash)
    return intent_id


def _events(world: _World) -> list[str]:
    return [name for name, _ in world.events]


async def _outcome(intent_id: int) -> str | None:
    intent = await fetch_intent(intent_id)
    assert intent is not None
    return intent.outcome


def _quarantined() -> list[str]:
    folder = settings.session_vault.quarantine_dir
    return sorted(p.name.split("-", 1)[1] for p in folder.glob("*")) if folder.exists() else []


# ---- re-key: the swap landed ------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("with_aside", [True, False])
async def test_a_swap_that_landed_stores_the_old_hash_and_tidies(
    world: _World, *, with_aside: bool
) -> None:
    write_session_file(_sessions() / "acc.session", key=_NEW)
    write_session_file(_sessions() / "acc_rekey.session", key=_NEW)
    if with_aside:
        write_session_file(_sessions() / "acc.session.rekey-aside", key=_OLD)
    intent_id = await _rekey_intent(api_id=7)

    assert await repair_key_intents() == 1

    stored = await fetch_rekey("acc")
    assert stored is not None
    # Nothing ends the old key on its own: the new key was never verified by this process.
    assert (stored.old_hash, stored.close_after, stored.closed_at) == ("111", None, None)
    assert await _outcome(intent_id) == "recovered"
    assert not (_sessions() / "acc_rekey.session").exists()
    assert not (_sessions() / "acc.session.rekey-aside").exists()
    # The aside may be the only copy of the old key: moved, never deleted.
    assert _quarantined() == (["acc.session.rekey-aside"] if with_aside else [])
    assert "session_rekey_recovered" in _events(world)
    device = await fetch_device_fingerprint("acc", "desktop")
    assert device is not None
    assert device.api_id == 7


@pytest.mark.asyncio
async def test_a_changed_account_file_beside_a_scratch_with_its_own_key_is_not_a_landing(
    world: _World,
) -> None:
    # Something else replaced the account's file: this re-key's authorization is still live
    # in the scratch, so it is logged out, and no old key is recorded for a swap that never was.
    write_session_file(_sessions() / "acc.session", key=b"something-else")
    write_session_file(_sessions() / "acc_rekey.session", key=_NEW)
    await _rekey_intent()

    await repair_key_intents()

    assert [call[2] for call in world.end_calls] == ["acc_rekey"]
    assert "session_rekey_recovered" not in _events(world)
    assert await fetch_rekey("acc") is None


@pytest.mark.asyncio
async def test_a_landed_swap_does_not_replace_a_row_that_already_names_the_old_key() -> None:
    write_session_file(_sessions() / "acc.session", key=_NEW)
    kept = StoredRekey(
        account_id="acc",
        old_hash="111",
        rekeyed_at="2026-11-01T00:00:00+00:00",
        close_after="2026-11-02T00:00:00+00:00",
    )
    await save_rekey(kept)
    await _rekey_intent()

    await repair_key_intents()

    assert await fetch_rekey("acc") == kept


# ---- re-key: the swap did not land ------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unlanded_swap_with_a_keyed_scratch_ends_it_by_logging_it_out(
    world: _World,
) -> None:
    write_session_file(_sessions() / "acc.session", key=_OLD)
    write_session_file(_sessions() / "acc_rekey.session", key=_NEW)
    intent_id = await _rekey_intent()

    assert await repair_key_intents("acc") == 1

    assert world.end_calls == [("acc", "desktop", "acc_rekey", None)]
    assert await _outcome(intent_id) == "undone"
    assert "session_rekey_interrupted" in _events(world)
    assert await fetch_rekey("acc") is None


@pytest.mark.asyncio
async def test_a_logout_that_cannot_be_confirmed_keeps_the_file_and_the_intent_open(
    world: _World,
) -> None:
    write_session_file(_sessions() / "acc.session", key=_OLD)
    write_session_file(_sessions() / "acc_rekey.session", key=_NEW)
    intent_id = await _rekey_intent()
    world.ended = False

    assert await repair_key_intents() == 0

    assert (_sessions() / "acc_rekey.session").exists()
    assert await _outcome(intent_id) is None
    world.ended = True
    # The operator's retry: the background pass would wait before trying this one again.
    assert await repair_key_intents("acc") == 1
    assert await _outcome(intent_id) == "undone"


@pytest.mark.asyncio
async def test_an_unlanded_swap_with_an_unkeyed_scratch_just_removes_it(world: _World) -> None:
    write_session_file(_sessions() / "acc.session", key=_OLD)
    write_session_file(_sessions() / "acc_rekey.session", key=None)
    intent_id = await _rekey_intent(stage="started", old_hash=None)

    assert await repair_key_intents() == 1

    assert world.end_calls == []
    assert not (_sessions() / "acc_rekey.session").exists()
    assert await _outcome(intent_id) == "undone"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "leftover"), [("started", False), ("signed_in", True), ("swapped", True)]
)
async def test_an_unlanded_swap_whose_scratch_is_gone_names_a_leftover_authorization(
    world: _World, stage: KeyIntentStage, *, leftover: bool
) -> None:
    write_session_file(_sessions() / "acc.session", key=_OLD)
    intent_id = await _rekey_intent(stage=stage)

    assert await repair_key_intents() == 1

    assert await _outcome(intent_id) == "undone"
    assert ("session_leftover_authorization" in _events(world)) is leftover
    if leftover:
        extra = dict(world.events)["session_leftover_authorization"]
        assert (extra["hash_suffix"], extra["kind"]) == ("222", "rekey")


@pytest.mark.asyncio
@pytest.mark.parametrize("same", [True, False])
async def test_an_unlanded_swap_tidies_or_quarantines_its_side_copy(
    world: _World, *, same: bool
) -> None:
    write_session_file(_sessions() / "acc.session", key=_OLD)
    write_session_file(_sessions() / "acc.session.rekey-aside", key=_OLD if same else b"another")
    await _rekey_intent(stage="started", old_hash=None)

    await repair_key_intents()

    assert not (_sessions() / "acc.session.rekey-aside").exists()
    assert _quarantined() == ([] if same else ["acc.session.rekey-aside"])
    assert world.end_calls == []


@pytest.mark.asyncio
async def test_an_unreadable_account_file_leaves_the_intent_open_and_says_so_once(
    world: _World,
) -> None:
    intent_id = await _rekey_intent()  # no ``acc.session`` at all

    assert await repair_key_intents() == 0
    assert await repair_key_intents() == 0

    assert await _outcome(intent_id) is None
    assert _events(world).count("flow_intent_unresolved") == 1


@pytest.mark.asyncio
async def test_a_flow_that_holds_the_auth_lock_is_left_alone(world: _World) -> None:
    write_session_file(_sessions() / "acc.session", key=_OLD)
    write_session_file(_sessions() / "acc_rekey.session", key=_NEW)
    intent_id = await _rekey_intent()

    async with account_auth_lock("acc"):
        assert await repair_key_intents() == 0

    assert world.end_calls == []
    assert await _outcome(intent_id) is None


@pytest.mark.asyncio
async def test_one_intent_that_fails_does_not_stop_the_rest(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_session_file(_sessions() / "acc.session", key=_OLD)
    first = await _rekey_intent(stage="started", old_hash=None)
    second = await open_intent(
        account_id="acc", kind="sign_in", device_type="desktop", session_name="acc"
    )
    real = _key_repair._repair_one

    async def _broken_first(intent: object) -> bool:
        if getattr(intent, "id", None) == first:
            raise OSError
        return await real(intent)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(_key_repair, "_repair_one", _broken_first)

    assert await repair_key_intents() == 1

    assert await _outcome(first) is None
    assert await _outcome(second) == "done"
    assert "flow_intent_repair_failed" in _events(world)


# ---- second device ----------------------------------------------------------------------


async def _second_intent(stage: KeyIntentStage = "signed_in") -> int:
    from core.repositories.account_key_intents import advance_intent  # noqa: PLC0415

    fingerprint = generate_android_fingerprint("acc", "+5215550001111")
    intent_id = await open_intent(
        account_id="acc",
        kind="second_device",
        device_type="mobile",
        session_name="acc_mobile",
        device_json=fingerprint.model_dump_json(),
    )
    await advance_intent(intent_id, stage=stage, new_hash="333")
    return intent_id


@pytest.mark.asyncio
async def test_a_recorded_second_device_closes_its_intent_done(world: _World) -> None:
    write_session_file(_sessions() / "acc_mobile.session", key=_NEW)
    await record_second_device("acc_mobile", generate_android_fingerprint("acc", "+5215550001111"))
    intent_id = await _second_intent()

    assert await repair_key_intents() == 1

    assert await _outcome(intent_id) == "done"
    assert world.end_calls == []
    account = await fetch_account("acc")
    assert account is not None
    assert account.second_session_name == "acc_mobile"


@pytest.mark.asyncio
async def test_a_second_device_file_that_was_never_recorded_is_logged_out_with_its_fingerprint(
    world: _World,
) -> None:
    write_session_file(_sessions() / "acc_mobile.session", key=_NEW)
    intent_id = await _second_intent()

    assert await repair_key_intents() == 1

    ((account_id, device_type, name, device),) = world.end_calls
    assert (account_id, device_type, name) == ("acc", "mobile", "acc_mobile")
    assert getattr(device, "device_type", None) == "mobile"
    assert await _outcome(intent_id) == "undone"
    assert "second_device_interrupted" in _events(world)


@pytest.mark.asyncio
async def test_a_second_device_logout_that_is_not_confirmed_stays_open(world: _World) -> None:
    write_session_file(_sessions() / "acc_mobile.session", key=_NEW)
    intent_id = await _second_intent()
    world.ended = False

    assert await repair_key_intents() == 0

    assert (_sessions() / "acc_mobile.session").exists()
    assert await _outcome(intent_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("stage", "leftover"), [("started", False), ("signed_in", True)])
async def test_a_second_device_whose_file_is_gone_is_undone(
    world: _World, stage: KeyIntentStage, *, leftover: bool
) -> None:
    intent_id = await _second_intent(stage)

    assert await repair_key_intents() == 1

    assert await _outcome(intent_id) == "undone"
    assert ("session_leftover_authorization" in _events(world)) is leftover


# ---- sign-in with the new API -----------------------------------------------------------


async def _sign_in_intent(kind: KeyIntentKind = "sign_in") -> int:
    return await open_intent(
        account_id="acc", kind=kind, device_type="desktop", session_name="acc", api_id=7
    )


@pytest.mark.asyncio
async def test_a_key_made_with_the_new_api_keeps_it_after_a_crash(world: _World) -> None:
    write_session_file(_sessions() / "acc.session", key=_NEW)
    intent_id = await _sign_in_intent()

    assert await repair_key_intents() == 1

    device = await fetch_device_fingerprint("acc", "desktop")
    assert device is not None
    assert device.api_id == 7
    assert await _outcome(intent_id) == "recovered"
    assert "sign_in_api_recovered" in _events(world)


@pytest.mark.asyncio
@pytest.mark.parametrize("has_key", [True, False])
async def test_a_sign_in_with_nothing_to_record_is_just_closed(
    world: _World, *, has_key: bool
) -> None:
    write_session_file(_sessions() / "acc.session", key=_NEW if has_key else None)
    if has_key:
        await set_device_api_id("acc", "desktop", 7)
    intent_id = await _sign_in_intent()

    assert await repair_key_intents() == 1

    assert await _outcome(intent_id) == "done"
    assert "sign_in_api_recovered" not in _events(world)
    device = await fetch_device_fingerprint("acc", "desktop")
    assert device is not None
    assert device.api_id == (7 if has_key else None)


# ---- legacy leftovers (files from before the journal) -----------------------------------


async def _legacy_account() -> object:
    account = await fetch_account("acc")
    assert account is not None
    return account


@pytest.mark.asyncio
async def test_a_legacy_scratch_that_holds_the_accounts_key_records_the_old_key_open(
    world: _World,
) -> None:
    write_session_file(_sessions() / "acc.session", key=_NEW)
    write_session_file(_sessions() / "acc_rekey.session", key=_NEW)

    landed = await _key_repair_rekey.settle_legacy_leftover(await _legacy_account(), "acc_rekey")  # ty: ignore[invalid-argument-type]

    assert landed is True
    stored = await fetch_rekey("acc")
    assert stored is not None
    assert (stored.old_hash, stored.closed_at, stored.close_after) == ("0", None, None)
    assert not (_sessions() / "acc_rekey.session").exists()
    assert world.end_calls == []


@pytest.mark.asyncio
async def test_a_legacy_scratch_does_not_replace_a_row_that_is_still_open() -> None:
    write_session_file(_sessions() / "acc.session", key=_NEW)
    write_session_file(_sessions() / "acc_rekey.session", key=_NEW)
    open_row = StoredRekey(account_id="acc", old_hash="111", rekeyed_at="2026-11-01T00:00:00+00:00")
    await save_rekey(open_row)

    assert await _key_repair_rekey.settle_legacy_leftover(await _legacy_account(), "acc_rekey")  # ty: ignore[invalid-argument-type]

    assert await fetch_rekey("acc") == open_row


@pytest.mark.asyncio
async def test_a_legacy_scratch_with_another_key_is_logged_out_and_the_rekey_goes_on(
    world: _World,
) -> None:
    write_session_file(_sessions() / "acc.session", key=_OLD)
    write_session_file(_sessions() / "acc_rekey.session", key=_NEW)

    landed = await _key_repair_rekey.settle_legacy_leftover(await _legacy_account(), "acc_rekey")  # ty: ignore[invalid-argument-type]

    assert landed is False
    assert [call[:3] for call in world.end_calls] == [("acc", "desktop", "acc_rekey")]
    assert not (_sessions() / "acc_rekey.session").exists()


@pytest.mark.asyncio
async def test_a_legacy_scratch_that_cannot_be_ended_still_refuses_the_rekey(
    world: _World,
) -> None:
    write_session_file(_sessions() / "acc.session", key=_OLD)
    write_session_file(_sessions() / "acc_rekey.session", key=_NEW)
    world.ended = False

    with pytest.raises(AccountActionError) as refused:
        await _key_repair_rekey.settle_legacy_leftover(await _legacy_account(), "acc_rekey")  # ty: ignore[invalid-argument-type]

    assert refused.value.code == "rekey_session_conflict"
    assert (_sessions() / "acc_rekey.session").exists()


@pytest.mark.asyncio
async def test_no_scratch_means_nothing_in_the_way(world: _World) -> None:
    write_session_file(_sessions() / "acc.session", key=_OLD)

    account = await _legacy_account()
    assert await _key_repair_rekey.settle_legacy_leftover(account, "acc_rekey") is False  # ty: ignore[invalid-argument-type]

    assert world.end_calls == []
