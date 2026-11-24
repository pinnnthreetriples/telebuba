"""What the card checks before the operator ends a re-keyed account's old authorization.

Why: on 2026-11-14 an account's own key died about a second after Telebuba ended its old
authorization. The key that did the ending was a six-day-old session of an unofficial app;
the one it ended was a 2.5-month-old official Telegram Desktop session, on an account
SpamBot had limited. That is what an account takeover looks like to Telegram, and nothing
Telebuba knows can bring such a key back. So the old authorization is ended only by the
operator (unless ``SESSION_AUDIT__AUTO_CLOSE_OLD_KEY`` says otherwise), and the card shows
first whether the account is in a state where losing the key is unlikely and recoverable:

* ``spam_clean``: SpamBot's last answer is ``clean`` and none in the window said otherwise;
* ``session_age``: the new session is old enough (Telegram's own minimum is a day);
* ``fallback_session``: a second device of ours is signed in, where a login code could
  arrive if the key dies. A Telegram Web login does not count: Telegram has been seen ending
  ours within a day or two, so it is not a channel to rely on;
* ``twofa_stored`` / ``passkey_stored``: what a fresh login would need, and proven to be
  there. ``twofa_stored`` is a password Telegram ACCEPTED (the current column), never the
  seller's unverified one. ``passkey_stored`` is the passkey row AND a vault that answers
  that its signing key exists (one local call; a sealed or unreachable vault is not ok).
  Neither costs a Telegram call.

Read-only: the items only inform, the operator decides.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.repositories._accounts_twofa import fetch_account_twofa_pair
from core.repositories.lifecycle import list_lifecycle_events
from core.repositories.spam_status import get_spam_status
from schemas.session_rekey import OldKeyCheck
from services.accounts._session_classify import parse_moment
from services.accounts.passkey import check_passkey_vault

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.session_rekey import StoredRekey

__all__ = ["old_key_checklist"]

_CLEAN = "clean"
_VERDICT = "spambot_verdict"


async def _spam_clean(account_id: str, now: datetime) -> bool:
    """SpamBot's last answer is clean, and no answer in the window said anything else."""
    verdict = await get_spam_status(account_id)
    if verdict is None or verdict.status != _CLEAN:
        return False
    since = now - timedelta(days=settings.session_audit.old_key_spam_clean_days)
    for event in await list_lifecycle_events(account_id):
        moment = parse_moment(event.created_at)
        if event.kind != _VERDICT or moment is None or moment < since:
            continue
        if event.extra.get("status") != _CLEAN:
            return False
    return True


def _old_enough(rekey: StoredRekey, now: datetime) -> bool:
    rekeyed = parse_moment(rekey.rekeyed_at)
    minimum = timedelta(days=settings.session_audit.old_key_min_age_days)
    return rekeyed is not None and now - rekeyed >= minimum


async def _passkey_usable(account_id: str) -> bool:
    """The passkey row exists and the vault confirms its key; anything else is not ok.

    The card must render whatever the vault does: an answer the probe was not written for
    (a proxy's 200 with a body that is not Vault's) reads as "not ok", never as a 500.
    """
    try:
        return await check_passkey_vault(account_id) == "ok"
    except Exception:  # noqa: BLE001 - an unreadable vault proves nothing; the item stays red.
        return False


async def old_key_checklist(
    account: AccountRead, rekey: StoredRekey, *, now: datetime | None = None
) -> list[OldKeyCheck]:
    """The checklist for ``account``'s open old authorization, in the order the card shows it."""
    moment = now or datetime.now(UTC)
    account_id = account.account_id
    fallback = account.second_session_name is not None
    current_password, _candidate = await fetch_account_twofa_pair(account_id)
    return [
        OldKeyCheck(name="spam_clean", ok=await _spam_clean(account_id, moment)),
        OldKeyCheck(name="session_age", ok=_old_enough(rekey, moment)),
        OldKeyCheck(name="fallback_session", ok=fallback),
        OldKeyCheck(name="twofa_stored", ok=current_password is not None),
        OldKeyCheck(name="passkey_stored", ok=await _passkey_usable(account_id)),
    ]
