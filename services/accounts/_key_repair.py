"""Repair what an interrupted key flow left behind: the intent journal, finished or undone.

A process crash, or an unlucky failure, in the middle of a flow that makes a NEW Telegram auth
key (re-key, second device, a sign-in with the new API) used to leave a scratch ``.session``,
a live authorization nobody lists, or a key connected with the wrong API for ever. Each such
flow writes an intent first (``core.repositories.account_key_intents``); an intent that is
still OPEN is repaired here, from the files and the journal, never from guesses:

* ``rekey``: see ``_key_repair_rekey`` (the swap landed, or it did not);
* ``second_device``: recorded on the account (done), or its file is a leftover whose
  authorization is ended by logging that file out, or its file is gone;
* ``sign_in``: the API the key was last connected with stays.

An intent whose account holds its auth lock is a live flow and is skipped. The repair never
raises (each intent is guarded, the exception class is the only thing logged) and never
deletes a key that may be the only copy of one. Run by the session guard on every pass, and
at the start of the actions an operator retries, so a retry heals first.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, cast

from core.db import fetch_account
from core.logging import log_event
from core.repositories.account_key_intents import close_intent, fetch_intent, list_open_intents
from core.repositories.device_fingerprint import fetch_device_fingerprint, set_device_api_id
from core.telegram_client import account_auth_lock, end_leftover_session
from schemas.device_fingerprint import DeviceFingerprint
from services.accounts import _key_repair_files as _files
from services.accounts._key_repair_rekey import (
    finish_rekey,
    recover_rekey,
    settle_legacy_leftover,
    settle_stale_aside,
)

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.device_fingerprint import DeviceType
    from schemas.key_intent import KeyIntent

__all__ = [
    "finish_rekey",
    "repair_key_intents",
    "reset_for_tests",
    "settle_legacy_leftover",
]

# Stdlib sink for the traceback; the persisted event carries only the error type.
logger = logging.getLogger(__name__)

# When the background pass may next try each intent that would not settle, and its wait.
_RETRY: dict[int, tuple[float, float]] = {}
_FIRST_RETRY_SECONDS = 900.0
_MAX_RETRY_SECONDS = 21600.0


def reset_for_tests() -> None:
    """Forget the retry schedule and which unresolved intents were reported."""
    _RETRY.clear()
    _files.reset_for_tests()


def _fingerprint_of(intent: KeyIntent) -> DeviceFingerprint | None:
    if intent.device_json is None:
        return None
    try:
        return DeviceFingerprint.model_validate_json(intent.device_json)
    except ValueError:
        return None


async def _second_device_file_ended(account: AccountRead, intent: KeyIntent) -> bool:
    """Whether a second device that was never recorded leaves nothing behind.

    Its file is gone, was never a session, or its authorization was just ended by logging the
    file out.
    """
    path = _files.session_file(intent.session_name)
    if path is None:
        await _files.unresolved(intent, "name")
        return False
    state = await _files.state_of(path)
    if state == "missing":
        if intent.stage != "started":
            await _files.leftover_authorization(intent)
        return True
    if state in {"no_key", "not_a_session"}:
        await _files.quarantine(path)
        return True
    if state == "unreadable":
        await _files.unresolved(intent, "file")
        return False
    ended = await end_leftover_session(
        account.account_id,
        cast("DeviceType", intent.device_type),
        intent.session_name,
        device=_fingerprint_of(intent),
    )
    if ended:
        await log_event("WARNING", "second_device_interrupted", account_id=account.account_id)
    return ended


async def _repair_second_device(account: AccountRead, intent: KeyIntent) -> bool:
    if account.second_session_name == intent.session_name:
        await close_intent(intent.id, "done")
        return True
    if not await _second_device_file_ended(account, intent):
        return False
    await close_intent(intent.id, "undone")
    return True


async def _repair_sign_in(account: AccountRead, intent: KeyIntent) -> bool:
    """The API the key was last connected with stays: a key made with the new API keeps it.

    The journal cannot say whether the sign-in landed. Either way the new API is right: such
    an intent is opened only for a key the account's status called gone, so the next sign-in
    would connect that file with the new API again, exactly as this one did or was about to.
    """
    path = _files.session_file(intent.session_name)
    device = await fetch_device_fingerprint(
        account.account_id, cast("DeviceType", intent.device_type)
    )
    if (
        path is not None
        and device is not None
        and device.api_id is None
        and intent.api_id is not None
        and await _files.state_of(path) == "ok"
    ):
        await set_device_api_id(account.account_id, intent.device_type, intent.api_id)
        await close_intent(intent.id, "recovered")
        await log_event("WARNING", "sign_in_api_recovered", account_id=account.account_id)
        return True
    await close_intent(intent.id, "done")
    return True


async def _repair_one(intent: KeyIntent) -> bool:
    account = await fetch_account(intent.account_id)
    if account is None:
        await _files.unresolved(intent, "account")
        return False
    if intent.kind == "rekey":
        return await recover_rekey(account, intent)
    if intent.kind == "second_device":
        return await _repair_second_device(account, intent)
    return await _repair_sign_in(account, intent)


def _waiting(intent_id: int, *, paced: bool) -> bool:
    """Whether the background pass tried this intent too recently to try again."""
    if not paced:
        return False
    waits = _RETRY.get(intent_id)
    return waits is not None and time.monotonic() < waits[0]


def _tried(intent_id: int, *, settled: bool) -> None:
    """Remember an attempt: a settled intent is forgotten, another waits twice as long."""
    if settled:
        _RETRY.pop(intent_id, None)
        return
    delay = min(_RETRY.get(intent_id, (0.0, _FIRST_RETRY_SECONDS / 2))[1] * 2, _MAX_RETRY_SECONDS)
    _RETRY[intent_id] = (time.monotonic() + delay, delay)


async def repair_key_intents(account_id: str | None = None) -> int:
    """Settle every open intent (of one account, or all) whose flow is not running; closed count.

    The background pass (no ``account_id``) spaces its tries at an intent that would not
    settle (a leftover behind a dead proxy is a Telegram connection each time): 15 minutes,
    doubling to 6 hours. An operator's retry (one account) always tries.
    """
    closed = 0
    paced = account_id is None
    for listed in await list_open_intents(account_id):
        lock = account_auth_lock(listed.account_id)
        if lock.locked() or _waiting(listed.id, paced=paced):
            continue
        try:
            async with lock:
                intent = await fetch_intent(listed.id)
                if intent is None or intent.closed_at is not None:
                    continue
                settled = await _repair_one(intent)
                closed += int(settled)
                _tried(listed.id, settled=settled)
        except Exception as exc:  # noqa: BLE001 - one intent never stops the rest.
            _tried(listed.id, settled=False)
            logger.warning("key intent repair failed (%s)", type(exc).__name__)
            await log_event(
                "WARNING",
                "flow_intent_repair_failed",
                account_id=listed.account_id,
                extra={"kind": listed.kind, "error_type": type(exc).__name__},
            )
    if account_id is not None:
        await _settle_side_copy(account_id)
    return closed


async def _settle_side_copy(account_id: str) -> None:
    """Before an operator's retry: the side copy a finished re-key left behind is put away."""
    lock = account_auth_lock(account_id)
    if lock.locked():
        return
    try:
        async with lock:
            if await list_open_intents(account_id):
                return
            account = await fetch_account(account_id)
            if account is not None and await settle_stale_aside(account):
                await log_event("WARNING", "session_rekey_aside_put_away", account_id=account_id)
    except Exception as exc:  # noqa: BLE001 - the retry itself refuses over what is left.
        logger.warning("stale side copy not settled (%s)", type(exc).__name__)
