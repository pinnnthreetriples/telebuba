"""Finishing and repairing a re-key the process did not get to finish.

Split out of ``_key_repair`` (file size). A re-key is a swap of the account's own session file
and two Telegram authorizations; a crash can stop it anywhere in between. The intent journal
(``account_key_intents``) says what was in flight, and the one-way ``old_key_mark`` of the
account's own file says whether the swap LANDED (the file now holds another key):

* landed: the new key is the account's; the old authorization's hash is stored from the
  journal (nothing ends the old key automatically: this process never verified the new one);
* not landed: the old key still is; whatever authorization the scratch file holds is ended
  by logging that file out, and only a confirmed end removes the file.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from core.logging import log_event
from core.repositories.account_key_intents import close_intent, fetch_intent, list_open_intents
from core.repositories.account_rekeys import fetch_rekey, save_rekey
from core.repositories.device_fingerprint import set_device_api_id
from core.telegram_client import end_leftover_session, same_session_key, session_key_mark
from schemas.key_intent import KeyIntent
from schemas.session_rekey import StoredRekey
from services.accounts import _key_repair_files as _files
from services.accounts._result import AccountActionError

if TYPE_CHECKING:
    from pathlib import Path

    from schemas.accounts import AccountRead
    from schemas.device_fingerprint import DeviceType
    from schemas.key_intent import KeyIntentOutcome
    from schemas.session_rekey import RekeyResult

# A row stored with the asking session's own entry: the old key is named later from the list.
_ASKING_HASH = "0"
_CONFLICT = "rekey_session_conflict"


def _now() -> datetime:
    return datetime.now(UTC)


async def finish_rekey(
    account_id: str,
    intent: KeyIntent,
    *,
    close_after: str | None = None,
    outcome: KeyIntentOutcome | None = "done",
) -> StoredRekey:
    """Record what a landed re-key needs, idempotently; the account's row.

    1. the API the new key was made with, when the journal knows one;
    2. the ``account_rekeys`` row for the old authorization, unless the account's row already
       names that hash (a retry, or the service that got there first);
    3. the intent closed with ``outcome`` (``None`` leaves it to the caller).

    ``close_after`` is when the old authorization may first be tried: the service passes now
    when it ends the old key on its own, the repair never does.
    """
    if intent.api_id is not None:
        await set_device_api_id(account_id, intent.device_type, intent.api_id)
    current = await fetch_rekey(account_id)
    if current is not None and current.old_hash == intent.old_hash:
        stored = current
    else:
        stored = StoredRekey(
            account_id=account_id,
            old_hash=intent.old_hash or _ASKING_HASH,
            rekeyed_at=_now().isoformat(),
            close_after=close_after,
        )
        await save_rekey(stored)
    if outcome is not None and intent.id > 0:
        await close_intent(intent.id, outcome)
    return stored


async def finish_gateway_rekey(
    account_id: str,
    device: DeviceType,
    scratch_name: str,
    result: RekeyResult,
    *,
    close_after: str | None,
) -> StoredRekey:
    """:func:`finish_rekey` for the normal path: the gateway returned, the journal is read back.

    A gateway that journaled nothing (no ``intent_id``) gets a stand-in intent, so the row is
    stored the same way.
    """
    intent = None if result.intent_id is None else await fetch_intent(result.intent_id)
    if intent is None:
        intent = KeyIntent(
            id=0,
            account_id=account_id,
            kind="rekey",
            device_type=device,
            session_name=scratch_name,
            stage="swapped",
            started_at=_now().isoformat(),
        )
    intent = intent.model_copy(update={"old_hash": result.old_hash})
    return await finish_rekey(account_id, intent, close_after=close_after)


async def _tidy_landed(aside: Path, scratch: Path, final: Path) -> None:
    """After a swap that landed: the side copy goes to the quarantine, the scratch copy goes.

    The side copy may be the only copy of the old key, so it is never deleted. The scratch
    file is removed only when it provably holds the key the account's file holds now.
    """
    await _files.quarantine(aside)
    if await same_session_key(str(scratch), str(final)):
        await _files.unlink(scratch)
    else:
        await _files.quarantine(scratch)


async def _end_scratch(account: AccountRead, intent: KeyIntent, scratch: Path) -> bool:
    """Take the scratch file's authorization out of the world; ``True`` once nothing is left."""
    state = await _files.state_of(scratch)
    if state == "missing":
        if intent.stage != "started":
            await _files.leftover_authorization(intent)
        return True
    if state in {"no_key", "not_a_session"}:
        await _files.quarantine(scratch)
        return True
    if state == "unreadable":
        return False
    ended = await end_leftover_session(
        account.account_id, cast("DeviceType", intent.device_type), intent.session_name
    )
    if ended:
        await log_event(
            "WARNING",
            "session_rekey_interrupted",
            account_id=account.account_id,
            extra={"stage": intent.stage},
        )
    return ended


async def _foreign_scratch(scratch: Path, final: Path) -> bool:
    """Whether the scratch file holds a key the account's file does NOT hold.

    A swap that landed copied the scratch into the account's file (the two hold one key) or
    went on to remove the scratch. A scratch with a key of its own next to a changed account
    file means something ELSE replaced that file (another flow, the operator): this re-key
    did not land, and its authorization is still live in the scratch.
    """
    return await _files.state_of(scratch) == "ok" and not await same_session_key(
        str(scratch), str(final)
    )


async def recover_rekey(account: AccountRead, intent: KeyIntent) -> bool:
    """Settle one open re-key intent from the files; ``True`` once it is closed."""
    final = _files.own_file(account)
    scratch = _files.session_file(intent.session_name)
    if final is None or scratch is None:
        await _files.unresolved(intent, "name")
        return False
    aside = _files.aside_of(final)
    mark = await asyncio.to_thread(session_key_mark, str(final))
    if mark is None or intent.old_key_mark is None:
        await _files.unresolved(intent, "file")
        return False
    if mark != intent.old_key_mark and not await _foreign_scratch(scratch, final):
        if not intent.old_hash:
            await _files.unresolved(intent, "changed")
            return False
        # The swap landed: the account's file holds a key this re-key made.
        await finish_rekey(account.account_id, intent, close_after=None, outcome=None)
        await _tidy_landed(aside, scratch, final)
        await close_intent(intent.id, "recovered")
        await log_event(
            "WARNING",
            "session_rekey_recovered",
            account_id=account.account_id,
            extra={"old_hash_suffix": intent.old_hash[-4:]},
        )
        return True
    # The swap did not land: the account's file still holds the old key (or one this re-key
    # never made), and the scratch file is the only handle on what it did make.
    if await asyncio.to_thread(aside.exists):
        if await same_session_key(str(aside), str(final)):
            await _files.unlink(aside)
        else:
            await _files.quarantine(aside)
    if not await _end_scratch(account, intent, scratch):
        return False
    await close_intent(intent.id, "undone")
    return True


async def settle_legacy_leftover(account: AccountRead, scratch_name: str) -> bool:
    """A scratch file from before the journal, with no intent: settle it before a re-key.

    ``True``: the old swap had landed (the scratch holds the account's key). Its old key is
    recorded for the operator (``old_hash="0"``, named later from the session list) and no new
    re-key starts over it. ``False``: nothing is left in the way. Raises ``rekey_session_conflict``
    when the scratch holds an authorization that cannot be ended now.
    """
    scratch = _files.session_file(scratch_name)
    final = _files.own_file(account)
    if scratch is None or final is None or not await asyncio.to_thread(scratch.exists):
        return False
    if any(i.session_name == scratch_name for i in await list_open_intents(account.account_id)):
        raise AccountActionError(_CONFLICT)
    if await same_session_key(str(scratch), str(final)):
        current = await fetch_rekey(account.account_id)
        if current is None or current.closed_at is not None:
            await save_rekey(
                StoredRekey(
                    account_id=account.account_id,
                    old_hash=_ASKING_HASH,
                    rekeyed_at=_now().isoformat(),
                )
            )
        await _files.unlink(scratch)
        return True
    if not await _end_scratch_legacy(account, scratch_name, scratch):
        raise AccountActionError(_CONFLICT)
    return False


async def _end_scratch_legacy(account: AccountRead, scratch_name: str, scratch: Path) -> bool:
    state = await _files.state_of(scratch)
    if state in {"no_key", "not_a_session"}:
        await _files.quarantine(scratch)
        return True
    if state != "ok":
        return False
    return await end_leftover_session(account.account_id, account.session_device, scratch_name)
