"""The session files the key repair looks at: where they are and how they leave.

Split out of ``_key_repair`` (file size). Nothing here talks to Telegram, and nothing here
deletes a key that may be the only copy of one: a file that is not provably worthless goes
to the quarantine (``core.session_vault``), never to the bin.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.session_file import SessionFileState, inspect_session_file
from core.session_vault import quarantine_file
from core.telegram_client import REKEY_ASIDE_SUFFIX

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.key_intent import KeyIntent

# Stdlib sink for what is not worth an event.
logger = logging.getLogger(__name__)

# Intents already reported as unresolved in this process: one warning each, not one per pass.
_UNRESOLVED_LOGGED: set[int] = set()


def reset_for_tests() -> None:
    """Forget which unresolved intents were reported."""
    _UNRESOLVED_LOGGED.clear()


def session_file(name: str) -> Path | None:
    """``<session dir>/<name>.session``; ``None`` for a name that is not a plain file stem."""
    if not name or Path(name).name != name or name in {".", ".."}:
        return None
    return settings.telegram.session_dir / f"{name}.session"


def own_file(account: AccountRead) -> Path | None:
    """The account's own (primary) session file."""
    return session_file(account.session_name or account.account_id)


def aside_of(final: Path) -> Path:
    """The side copy a re-key's swap keeps of the file it replaces."""
    return Path(f"{final}{REKEY_ASIDE_SUFFIX}")


async def state_of(path: Path) -> SessionFileState:
    return await asyncio.to_thread(inspect_session_file, path)


async def quarantine(path: Path) -> None:
    """Move a file out of the sessions folder into the quarantine; a missing one is fine."""
    await asyncio.to_thread(quarantine_file, path)


def _unlink(path: Path) -> None:
    path.unlink(missing_ok=True)
    Path(f"{path}-journal").unlink(missing_ok=True)


async def unlink(path: Path) -> None:
    """Remove a file that is provably worth nothing (a copy of what another file holds)."""
    await asyncio.to_thread(_unlink, path)


async def unresolved(intent: KeyIntent, reason: str) -> None:
    """Report an intent the repair cannot decide yet; once per process for each."""
    if intent.id in _UNRESOLVED_LOGGED:
        return
    _UNRESOLVED_LOGGED.add(intent.id)
    await log_event(
        "WARNING",
        "flow_intent_unresolved",
        account_id=intent.account_id,
        extra={"kind": intent.kind, "stage": intent.stage, "reason": reason},
    )


async def leftover_authorization(intent: KeyIntent) -> None:
    """A new authorization exists whose file is gone: it shows as a foreign session."""
    await log_event(
        "WARNING",
        "session_leftover_authorization",
        account_id=intent.account_id,
        extra={"hash_suffix": (intent.new_hash or "")[-4:], "kind": intent.kind},
    )
