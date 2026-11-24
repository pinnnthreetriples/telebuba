"""Once per start: do the accounts' session files match the sessions folder?

A ``.session`` is the only copy of an account's key. A file that went missing while the app was
off (antivirus, a move) is told at the start, not at the first check; a file in the folder that
no account owns is counted. Reports only: nothing is deleted, repaired or created. A step that
fails is retried by the next pass (the guard's ``_guarded`` logs it), and one that finished is
not repeated until the next start. Events carry the account id or a count, never a path.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from core.config import settings
from core.logging import log_event
from core.repositories.accounts import list_accounts
from core.session_file import inspect_session_file, list_session_stems, session_file_of

# Stored statuses that expect no key in the file (see ``services.accounts.sessions``).
_NO_KEY_EXPECTED = frozenset({"new", "unauthorized"})
# ``not_a_session`` too (an empty file, garbage): the pool refuses it just the same.
_GONE = frozenset({"missing", "no_key", "not_a_session"})


@dataclass
class _State:
    done: bool = False


_state = _State()


def _gone_stems(stems: list[str]) -> bool:
    folder = settings.telegram.session_dir
    return any(inspect_session_file(session_file_of(str(folder / stem))) in _GONE for stem in stems)


async def run_file_reconciliation() -> None:
    """Log each account whose session file is gone, and how many files belong to no account."""
    if _state.done:
        return
    accounts = (await list_accounts()).accounts
    known: set[str] = set()
    for account in accounts:
        stems = [account.session_name or account.account_id]
        if account.second_session_name:
            stems.append(account.second_session_name)
        known.update(stems)
        if account.status not in _NO_KEY_EXPECTED and await asyncio.to_thread(_gone_stems, stems):
            await log_event(
                "WARNING", "account_session_file_missing", account_id=account.account_id
            )
    on_disk = await asyncio.to_thread(list_session_stems, settings.telegram.session_dir)
    orphans = sum(1 for stem in on_disk if stem not in known)
    if orphans:
        await log_event("WARNING", "session_orphan_files_found", extra={"count": orphans})
    _state.done = True


def reset_for_tests() -> None:
    global _state  # noqa: PLW0603 - one module-level runtime, reset between tests
    _state = _State()
