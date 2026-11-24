"""Which protections each account has, from stored facts only, for the Accounts table.

The operator's goal is that an account he bought stays his, so the table says per account
what is in place: a proxy, a copy of its session file, a key of its own that was re-keyed
(an imported seller key may have been kept), the old authorization ended, a verified cloud
password, a passkey and a session audit that found nobody else. Every source is read for
all the page's accounts at once (one query or one directory pass each); nothing here calls
Telegram or the key vault.

``own_key``: nothing stored tells an import from a sign-in Telebuba made itself (the
journal and the key-intent table cover only some flows, and the log is pruned), so a
re-key row is the only proof the key is not the seller's.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.repositories._accounts_passkey import list_passkey_account_ids
from core.repositories._accounts_twofa import list_verified_twofa_account_ids
from core.repositories.account_rekeys import list_rekeys
from core.repositories.session_audits import list_foreign_session_counts
from core.session_vault import list_versions, vault_enabled
from schemas.account_protection import PROTECTION_CHECKS, AccountProtection, ProtectionCheck

if TYPE_CHECKING:
    from collections.abc import Sequence

    from schemas.accounts import AccountRead

__all__ = ["protection_for"]


def _stems_with_copy(stems: Sequence[str]) -> set[str]:
    """The stems with at least one stored copy; a folder that cannot be read has none."""
    found: set[str] = set()
    for stem in stems:
        try:
            if list_versions(stem):
                found.add(stem)
        except OSError:
            continue
    return found


async def _copies(accounts: list[AccountRead]) -> set[str]:
    if not vault_enabled():
        return set()
    # The file a client opens: the stored session name, else the account id.
    return await asyncio.to_thread(
        _stems_with_copy, [a.session_name or a.account_id for a in accounts]
    )


async def protection_for(accounts: list[AccountRead]) -> dict[str, AccountProtection]:
    """The protection summary of each account, by account id."""
    if not accounts:
        return {}
    copies = await _copies(accounts)
    rekeys = {rekey.account_id: rekey for rekey in await list_rekeys()}
    twofa = await list_verified_twofa_account_ids()
    passkeys = await list_passkey_account_ids()
    foreign = await list_foreign_session_counts()
    result: dict[str, AccountProtection] = {}
    for account in accounts:
        account_id = account.account_id
        rekey = rekeys.get(account_id)
        passed: dict[ProtectionCheck, bool] = {
            "proxy": account.proxy_id is not None,
            "session_copy": (account.session_name or account_id) in copies,
            "own_key": rekey is not None,
            # An account without a re-key fails this too; a row with a problem is an old
            # key Telebuba gave up on: still open.
            "old_key_closed": rekey is not None
            and rekey.closed_at is not None
            and rekey.problem is None,
            "twofa_stored": account_id in twofa,
            "passkey": account_id in passkeys,
            # Never audited is missing, not clean.
            "audit_clean": foreign.get(account_id) == 0,
        }
        missing = [check for check in PROTECTION_CHECKS if not passed[check]]
        result[account_id] = AccountProtection(
            score=len(PROTECTION_CHECKS) - len(missing),
            total=len(PROTECTION_CHECKS),
            missing=missing,
        )
    return result
