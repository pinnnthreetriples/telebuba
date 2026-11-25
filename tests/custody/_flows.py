"""What every flow's crash matrix shares: account, journal seams, repair, invariants.

* I1: a live key is never lost: the server's live keys always include one that is on disk.
* I2: every authorization a flow created is the account's file, recorded, ended, or held by an
  intent that is still open or closed ``incomplete`` with its file kept.
* I3: no silent state: nothing open that repair will not look at again, no leftover that blocks
  the operator's retry.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import create_account, fetch_account, insert_device_fingerprint
from core.device_fingerprint import generate_random_device_fingerprint
from core.repositories.account_rekeys import fetch_rekey
from core.repositories.proxies import create_and_assign_proxy
from core.telegram_client import _key_intent, _leftover
from schemas.accounts import AccountCreate
from schemas.proxy import ProxyCreateAssignment
from services.accounts import _key_repair, _key_repair_files, _key_repair_rekey
from services.accounts._key_repair import repair_key_intents
from tests.custody._world import OLD, Fault, World, intent_rows, read_key
from tests.session_files import write_session_file

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

NEW_API_ID = 77
# (step, may it have taken effect when its answer is lost: ``True`` for both a raised error and a
# cancellation, ``"thread"`` for a worker thread that runs on after a cancellation only)
Steps = list[tuple[str, bool | str]]
# Telegram's authorizations a flow made that nobody recorded as the account's own.
_OPERATOR_EVENTS = {"flow_intent_unresolved", "flow_intent_repair_failed"}


def matrix(
    steps: Steps, *, shielded: Sequence[str] = (), known: dict[str, str] | None = None
) -> list[object]:
    """``(step) x (failure kind)`` as pytest params with legible ids.

    A step the answer to which can be lost is tried before and after its effect; a shielded
    step cannot be cancelled before it lands (production shields it), so only after. ``known``
    maps a case id to the one-sentence defect it is expected to show (a strict xfail).
    """
    params: list[object] = []
    for name, uncertain in steps:
        faults = []
        for kind in ("raised", "cancelled"):
            lands = uncertain is True or (uncertain == "thread" and kind == "cancelled")
            for landed in (False, True) if lands else (False,):
                if kind == "cancelled" and name in shielded and not landed:
                    continue
                faults.append(
                    Fault(name, kind, landed=landed or (kind == "cancelled" and name in shielded))
                )
        for fault in (*faults, Fault(name, "died")):
            marks = ()
            if known and fault.label in known:
                marks = (pytest.mark.xfail(strict=True, reason=known[fault.label]),)
            params.append(pytest.param(fault, id=fault.label, marks=marks))
    return params


async def seed_account(world: World) -> None:
    """Account ``acc`` with a proxy, a device and a session file holding the old live key."""
    await create_account(AccountCreate(account_id="acc", session_name="acc"))
    await insert_device_fingerprint(generate_random_device_fingerprint("acc"))
    await create_and_assign_proxy(
        ProxyCreateAssignment(proxy_type="socks5", host="10.0.0.9", port=1080, account_id="acc")
    )
    write_session_file(world.file("acc"), key=OLD)
    world.server.seed(OLD, 111)


def install_common(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """The seams every flow and the repair share: client factory, event sink, journal steps."""
    monkeypatch.setattr(_leftover, "create_telegram_client", world.factory)
    # A key made now may be made with the new API: the journal then carries its id.
    monkeypatch.setattr(settings.telegram, "new_api_id", NEW_API_ID)
    monkeypatch.setattr(settings.telegram, "new_api_hash", "new-api-hash")
    sink = world.log_stub()
    for module in (_key_repair, _key_repair_files, _key_repair_rekey):
        monkeypatch.setattr(module, "log_event", sink, raising=False)
    real_advance = _key_intent.advance_intent
    real_close = _key_intent.close_intent
    real_open = _key_intent.open_intent

    async def advance(intent_id: int, **fields: object) -> None:
        names = {"signed_in": "journal.signed_in", "swapped": "journal.swapped"}
        name = names.get(str(fields.get("stage")), "journal.old_hash")
        await world.step(name, lambda: real_advance(intent_id, **fields))  # ty: ignore[invalid-argument-type]

    async def close(intent_id: int, outcome: str) -> None:
        await world.step("journal.close", lambda: real_close(intent_id, outcome))  # ty: ignore[invalid-argument-type]

    async def open_(**fields: object) -> int:
        return await world.step("journal.open", lambda: real_open(**fields))  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(_key_intent, "advance_intent", advance)
    monkeypatch.setattr(_key_intent, "close_intent", close)
    monkeypatch.setattr(_key_intent, "open_intent", open_)


# --- observation, repair, invariants -------------------------------------------------------------


async def observe(world: World) -> dict[str, object]:
    """Everything repair may change, compared between its first and second pass."""
    account = await fetch_account("acc")
    rekey = await fetch_rekey("acc")
    return {
        "files": sorted(world.disk_keys().items()),
        "live": sorted(world.server.live),
        "intents": [(r["id"], r["stage"], r["outcome"]) for r in intent_rows()],
        "second": account.second_session_name if account else None,
        "rekey": None if rekey is None else (rekey.old_hash, rekey.problem),
    }


async def restart_and_repair(world: World) -> None:
    """Run the repair twice in a fresh process, then as the operator.

    The second pass must change nothing; the operator then
    retries the account's open intents, which are never paced.
    """
    world.phase = "repair"
    _key_repair.reset_for_tests()
    await repair_key_intents()
    first = await observe(world)
    await repair_key_intents()
    assert await observe(world) == first, f"repair is not idempotent: {describe(world)}"
    for row in intent_rows():
        if row["outcome"] is None:
            told = _OPERATOR_EVENTS & set(world.event_names())
            assert row["id"] in _key_repair._RETRY or told, (
                f"I3: open intent {row['id']} will not be looked at again: {describe(world)}"
            )
    await repair_key_intents("acc")


def describe(world: World) -> str:
    """The state a failing case needs to be read from."""
    rows = [
        (r["id"], r["kind"], r["session_name"], r["stage"], r["outcome"]) for r in intent_rows()
    ]
    return (
        f"outcome={world.outcome!r}\ntrace={world.trace}\nlive={sorted(world.server.live)}\n"
        f"created={world.server.created}\nfiles={sorted(world.disk_keys().items())}\n"
        f"intents={rows}\nevents={world.event_names()}"
    )


def open_or_incomplete() -> list[dict[str, object]]:
    return [r for r in intent_rows() if r["outcome"] in {None, "incomplete"}]


def assert_a_live_key_is_on_disk(world: World, when: str, *, none_live_ok: bool = False) -> None:
    """I1: some key Telegram still honours is on disk, in a place the repair knows.

    ``none_live_ok``: the account began with no live key (a sign-in re-authorizes a dead one).
    """
    if none_live_ok and not world.server.live:
        return
    on_disk = set(world.disk_keys().values())
    assert world.server.live & on_disk, f"I1 ({when}): no live key on disk: {describe(world)}"


async def assert_the_account_works(world: World) -> None:
    """I1, after repair: the account's own file holds a key Telegram honours."""
    own = read_key(world.file("acc"))
    assert own in world.server.live, f"I1: the account's file is dead\n{describe(world)}"


async def assert_all_accounted(world: World, recorded: Callable[[bytes], Awaitable[bool]]) -> None:
    """I2: each key a flow made and Telegram still honours is recorded, or its intent holds it."""
    held = {
        read_key(world.file(str(r["session_name"])))
        for r in open_or_incomplete()
        if r["account_id"] == "acc"
    }
    for key in world.server.created:
        if key in world.server.live and not await recorded(key) and key not in held:
            pytest.fail(
                f"I2: {key!r} is alive on the server, not recorded, held by no intent: "
                f"{describe(world)}"
            )


def assert_no_silent_state(world: World) -> None:
    """I3: an open intent is explained to the operator, or will be looked at again."""
    for row in intent_rows():
        if row["outcome"] is None:
            told = _OPERATOR_EVENTS & set(world.event_names())
            assert told, f"I3: intent {row['id']} is open and nobody was told: {describe(world)}"


async def retry(operation: Callable[[], Awaitable[object]], refusals: set[str]) -> None:
    """The operator's retry with nothing failing: any refusal must be a named, honest one."""
    try:
        await asyncio.wait_for(operation(), timeout=30)
    except AssertionError:
        raise
    except Exception as exc:  # noqa: BLE001 - a refusal's code is what is asserted
        code = getattr(exc, "code", None) or str(exc)
        assert code in refusals, f"the retry answered {code!r}"
