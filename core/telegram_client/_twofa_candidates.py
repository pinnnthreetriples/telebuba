"""Cloud-password sign-in that tries every password that may be in force.

A 2FA write whose outcome was never learnt (a crash, a lost reply) leaves the account with
two candidates: the current stored password and a pending one. The Telethon sign-in paths
try them in order, current first. Only ``PasswordHashInvalidError`` moves on to the next;
any other error stops, because it says nothing about the password. The one that works is
promoted if it was the pending one. Passwords are passed to Telegram only, never logged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon import errors

from core.repositories._accounts_twofa import confirm_twofa_password_used

if TYPE_CHECKING:
    from collections.abc import Sequence

    from telethon import TelegramClient

__all__ = ["sign_in_with_passwords"]


async def sign_in_with_passwords(
    client: TelegramClient, account_id: str, passwords: Sequence[str]
) -> None:
    """Finish a 2FA sign-in with the first of ``passwords`` Telegram accepts.

    Raises the last ``PasswordHashInvalidError`` when every one is rejected, and any other
    error at once. ``passwords`` must not be empty.
    """
    rejected: errors.PasswordHashInvalidError | None = None
    for password in dict.fromkeys(passwords):
        try:
            await client.sign_in(password=password)
        except errors.PasswordHashInvalidError as exc:
            rejected = exc
            continue
        await confirm_twofa_password_used(account_id, password)
        return
    if rejected is None:
        msg = "no 2FA password to try"
        raise ValueError(msg)
    raise rejected
