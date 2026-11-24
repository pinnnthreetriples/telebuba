"""Adversarial probes of the key-intent repair (invariants 2 and 3)."""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from core.config import settings
from core.db import create_account, insert_device_fingerprint
from core.device_fingerprint import generate_random_device_fingerprint
from core.repositories.account_key_intents import advance_intent, fetch_intent, open_intent
from core.repositories.account_rekeys import fetch_rekey
from core.telegram_client import account_auth_lock
from schemas.accounts import AccountCreate
from services.accounts import _key_repair, _key_repair_files, _key_repair_rekey
from services.accounts._key_repair import repair_key_intents
from tests.session_files import write_session_file

if TYPE_CHECKING:
    from collections.abc import Iterator

_OLD = b"old-key-bytes"
_NEW = b"new-key-bytes"


def _mark(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()


class _World:
    def __init__(self) -> None:
        self.end_calls: list[str] = []


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[_World]:
    state = _World()

    async def _log(*_a: object, **_k: object) -> None:
        return None

    async def _end(
        _account_id: str, _device_type: str, session_name: str, **_device: object
    ) -> bool:
        state.end_calls.append(session_name)
        (settings.telegram.session_dir / f"{session_name}.session").unlink(missing_ok=True)
        return True

    for module in (_key_repair, _key_repair_files, _key_repair_rekey):
        monkeypatch.setattr(module, "log_event", _log, raising=False)
    monkeypatch.setattr(_key_repair, "end_leftover_session", _end)
    monkeypatch.setattr(_key_repair_rekey, "end_leftover_session", _end)
    _key_repair.reset_for_tests()
    yield state
    _key_repair.reset_for_tests()


@pytest_asyncio.fixture(autouse=True)
async def _account(world: _World) -> None:  # noqa: ARG001
    await create_account(AccountCreate(account_id="acc", session_name="acc"))
    await insert_device_fingerprint(generate_random_device_fingerprint("acc"))


async def _rekey_intent(*, stage: str, old_hash: str | None, api_id: int | None = None) -> int:
    intent_id = await open_intent(
        account_id="acc",
        kind="rekey",
        device_type="desktop",
        session_name="acc_rekey",
        api_id=api_id,
        old_key_mark=_mark(_OLD),
    )
    await advance_intent(intent_id, stage=stage, old_hash=old_hash)  # ty: ignore[invalid-argument-type]
    return intent_id


@pytest.mark.asyncio
async def test_a_started_intent_without_an_old_hash_is_never_declared_landed() -> None:
    sessions = settings.telegram.session_dir
    write_session_file(sessions / "acc.session", key=_NEW)
    write_session_file(sessions / "acc_rekey.session", key=_NEW)
    intent_id = await _rekey_intent(stage="started", old_hash=None)

    assert await repair_key_intents("acc") == 0

    assert await fetch_rekey("acc") is None
    intent = await fetch_intent(intent_id)
    assert intent is not None
    assert intent.closed_at is None
    assert (sessions / "acc_rekey.session").exists()


@pytest.mark.asyncio
async def test_a_repair_called_by_the_task_that_holds_the_lock_skips_and_does_not_deadlock() -> (
    None
):
    await _rekey_intent(stage="signed_in", old_hash="111")
    write_session_file(settings.telegram.session_dir / "acc.session", key=_OLD)
    async with account_auth_lock("acc"):
        assert await asyncio.wait_for(repair_key_intents("acc"), 2) == 0


@pytest.mark.asyncio
async def test_a_foreign_file_does_not_make_the_repair_quarantine_a_live_scratch(
    world: _World,
) -> None:
    sessions = settings.telegram.session_dir
    write_session_file(sessions / "acc.session", key=b"third-key")
    write_session_file(sessions / "acc_rekey.session", key=_NEW)
    await _rekey_intent(stage="signed_in", old_hash="111", api_id=9)

    await repair_key_intents("acc")

    assert world.end_calls == ["acc_rekey"]
    assert await fetch_rekey("acc") is None
