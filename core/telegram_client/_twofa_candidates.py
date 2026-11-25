"""Cloud-password sign-in that tries every password that may be in force.

A 2FA write whose outcome was never learnt (a crash, a lost reply) leaves the account with
two candidates: the current stored password and a pending one. The Telethon sign-in paths
try them in order, current first. Only ``PasswordHashInvalidError`` moves on to the next;
any other error stops, because it says nothing about the password. The one that works is
promoted if it was the pending one. Passwords are passed to Telegram only, never logged.

Every wrong answer is counted against the account's budget (``core.twofa_budget``). The
budget is checked ONCE per attempt, before the first stored password is sent: with it spent
nothing stored is sent at all, and once the attempt started its remaining stored candidates
are tried (so a spent last unit cannot withhold a correct pending one; the overshoot is at
most one rejection per attempt). The password the operator typed (``typed``) skips the check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon import errors

from core.repositories._accounts_twofa import confirm_twofa_password_used
from core.twofa_budget import ensure_twofa_budget, record_password_rejection

if TYPE_CHECKING:
    from collections.abc import Sequence

    from telethon import TelegramClient

__all__ = ["sign_in_with_passwords"]


async def sign_in_with_passwords(
    client: TelegramClient,
    account_id: str,
    passwords: Sequence[str],
    attempted: list[str] | None = None,
    *,
    typed: str | None = None,
) -> str:
    """Finish a 2FA sign-in with the first of ``passwords`` Telegram accepts; return it.

    Raises the last ``PasswordHashInvalidError`` when every one is rejected, and any other
    error at once; ``TwofaAttemptsExhaustedError`` (nothing sent) when a stored password is
    first and the account's budget of wrong answers is spent. ``passwords`` must not be empty.
    Each password is appended to ``attempted`` BEFORE it is sent, so a caller cut off by a
    deadline knows which one was in flight. ``typed`` is the one the operator entered for
    this attempt: it skips the budget check.
    """
    rejected: errors.PasswordHashInvalidError | None = None
    budget_checked = False
    for password in dict.fromkeys(passwords):
        if password != typed and not budget_checked:
            await ensure_twofa_budget(account_id)
            budget_checked = True
        if attempted is not None:
            attempted.append(password)
        try:
            await client.sign_in(password=password)
        except errors.PasswordHashInvalidError as exc:
            rejected = exc
            await record_password_rejection(account_id)
            continue
        await confirm_twofa_password_used(account_id, password)
        return password
    if rejected is None:
        msg = "no 2FA password to try"
        raise ValueError(msg)
    raise rejected
