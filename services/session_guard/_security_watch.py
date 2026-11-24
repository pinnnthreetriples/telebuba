"""The security watch: the account's cloud-password settings, compared with the last read.

A password reset somebody requested (``pending_reset_date``: in seven days the cloud password
is REMOVED), a recovery e-mail that changed, or the password itself appearing or disappearing
are takeover steps nobody was told about, because the settings were read only when the
operator opened the card. The guard reads them after an answered audit and journals:

* ``twofa_reset_pending``: a reset is pending that the last snapshot did not have, also on
  the very first read (dangerous whatever came before);
* ``security_settings_changed``: which of ``has_password``, ``has_recovery``,
  ``email_unconfirmed`` differ from the snapshot (never on the first read, never the e-mail
  pattern itself, only the field names).

Telebuba's own changes forget the snapshot (``forget_security_snapshot``), so the next read
is a fresh baseline and not an alarm. Nothing here raises: an unreadable status is skipped.
"""

from __future__ import annotations

import logging

from core.logging import log_event
from core.repositories.account_security_snapshots import (
    fetch_security_snapshot,
    save_security_snapshot,
)
from schemas.security_snapshot import SecuritySnapshot
from services.accounts import AccountActionError, AccountNotFoundError, read_account_twofa

logger = logging.getLogger(__name__)

_FIELDS = ("has_password", "has_recovery", "email_unconfirmed")


async def _compare_and_store(account_id: str) -> None:
    view = await read_account_twofa(account_id)
    status = view.status
    if status is None:
        return
    current = SecuritySnapshot(
        has_password=status.has_password,
        has_recovery=status.has_recovery,
        pending_reset_date=status.pending_reset_date,
        email_unconfirmed=status.email_unconfirmed_pattern is not None,
    )
    previous = await fetch_security_snapshot(account_id)
    if current.pending_reset_date and (
        previous is None or previous.pending_reset_date != current.pending_reset_date
    ):
        await log_event(
            "WARNING",
            "twofa_reset_pending",
            account_id=account_id,
            extra={"pending_reset_date": current.pending_reset_date},
        )
    if previous is not None:
        changed = [name for name in _FIELDS if getattr(previous, name) != getattr(current, name)]
        if changed:
            await log_event(
                "WARNING",
                "security_settings_changed",
                account_id=account_id,
                extra={"changed": changed},
            )
    await save_security_snapshot(account_id, current)


async def check_security(account_id: str) -> None:
    """Read, compare, store, journal; never raises."""
    try:
        await _compare_and_store(account_id)
    except (AccountActionError, AccountNotFoundError):
        return
    except Exception as exc:  # noqa: BLE001 - a background watch must never end the pass
        logger.warning("security watch failed (%s)", type(exc).__name__)
