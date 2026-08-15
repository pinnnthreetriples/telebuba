"""Account lifecycle — registration and geo evaluation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.db import (
    create_account,
    delete_account,
    fetch_account,
    fetch_device_fingerprint,
    list_accounts,
)
from core.device_fingerprint import get_or_create_device_fingerprint
from core.logging import log_event
from core.phone_geo import evaluate_geo
from core.telegram_client import forget_post_listener, remove_account_session, removing_client
from schemas.geo import GeoMatch
from services.accounts._result import AccountNotFoundError

if TYPE_CHECKING:
    from schemas.accounts import AccountCreate, AccountRead

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
logger = logging.getLogger(__name__)


async def add_account(data: AccountCreate) -> AccountRead:
    account = await create_account(data)
    await get_or_create_device_fingerprint(account.account_id)
    saved = await list_accounts()
    persisted = next(
        (item for item in saved.accounts if item.account_id == account.account_id),
        account,
    )
    await log_event(
        "INFO",
        "account_added",
        account_id=persisted.account_id,
        extra={"session_name": persisted.session_name},
    )
    return persisted


async def require_account(account_id: str) -> AccountRead:
    """One account's read model, or :class:`AccountNotFoundError`.

    For routes whose service call deliberately degrades on a missing row instead
    of raising — ``spam_status.refresh_spam_status`` returns an uncached
    ``unknown`` verdict, because warming and neurocomment onboarding also call it
    and a hard raise there would change cycle behaviour. The route does the hard
    lookup so its HTTP surface answers 404 like its siblings while those two
    internal callers keep the soft path.
    """
    account = await fetch_account(account_id)
    if account is None:
        raise AccountNotFoundError(account_id)
    return account


async def remove_account(account_id: str) -> None:
    """Public delete: stop warming + purge DB rows under one lifecycle lock.

    The repo-level :func:`core.db.delete_account` only touches the DB; it has
    no knowledge of the in-process ``_RUNTIME`` task table that
    ``start_warming`` / ``reconcile_warming_runtime`` populate.

    Round 1 (P3.7) called ``stop_warming`` then ``delete_account``, but the
    lock dropped between those two steps — a concurrent ``start_warming``
    could slip in, create a fresh task, then have its account row deleted
    underneath it, producing an orphan loop. Round 2 (P2.2) closes that gap
    by holding the per-account lifecycle lock across stop AND delete via
    ``account_lock(account_id)`` and the lock-internal ``_stop_warming_locked``
    helper.

    Use this wrapper from UI / service callers. The ``_tdata`` rollback path
    keeps the bare repo call (those accounts were just created and never
    started warming, so there's no task to cancel).
    """
    # Local import to avoid a services→services import cycle at module load.
    from core.db import (  # noqa: PLC0415
        get_listener_account_id,
        set_listener_account_id,
        set_listener_running,
    )
    from services.neurocomment import _runtime as nc_runtime  # noqa: PLC0415
    from services.neurocomment._state import forget_account_cooldowns  # noqa: PLC0415
    from services.warming import (  # noqa: PLC0415
        WarmingTaskNotQuiescentError,
        _stop_warming_locked,
        account_lock,
    )

    # Lock order is global neurocomment lifecycle → per-account lifecycle everywhere.
    # This makes delete atomic against listener start/switch/reconcile and prevents the
    # in-memory handler from surviving a DB cascade or resurrecting on pool rebuild.
    async with nc_runtime.neurocomment_lifecycle(), account_lock(account_id):
        # The warming stop comes first because it is the only step here that can
        # REFUSE the delete. Nothing above it may have destroyed operator state by
        # then: run after the listener teardown, a 409 would answer "nothing was
        # deleted" while the listener account and its running flag were already
        # cleared, with no rollback and nothing in the response to say so.
        try:
            await _stop_warming_locked(account_id)
        except WarmingTaskNotQuiescentError:
            # A live task still owns process resources. Deleting its account or
            # session would turn it into an untracked ghost; retry after it exits.
            raise
        except Exception as exc:  # delete must not fail because the stop did.
            logger.exception("stop warming failed while removing %s", account_id)
            await log_event(
                "WARNING",
                "account_remove_stop_warming_failed",
                account_id=account_id,
                extra={"error_type": type(exc).__name__},
            )
        if await get_listener_account_id() == account_id:
            try:
                await nc_runtime.shutdown_neurocomment_runtime(account_id)
            finally:
                await set_listener_account_id(None)
                await set_listener_running(running=False)
        # Disconnect the pooled client so it stops holding the account's
        # ``.session`` handle open (Windows can't unlink a live handle), then
        # unlink the orphaned session file before purging the DB rows. The
        # lifecycle lock does not cover pool borrowers (they never take it), so
        # ``removing_client`` also has to refuse rebuilds until the row is gone —
        # otherwise a borrower waking mid-removal re-opens the ``.session`` file
        # and the unlink aborts the delete with PermissionError.
        async with removing_client(account_id):
            account = await fetch_account(account_id)
            if account is None:
                # The only lifecycle entry point that used to tolerate a missing
                # row, and the tolerance was the bug: ``session_name=None`` makes
                # ``_session_path`` fall back to ``session_dir / account_id``, so
                # an unvalidated id (``..\..\evil``) unlinked whatever that
                # resolved to. Guard, never sanitise; the API maps this to 404.
                raise AccountNotFoundError(account_id)
            await remove_account_session(account_id, account.session_name)
            await delete_account(account_id)
        # Only once the row is really gone: the listener registries are keyed by
        # account id and nothing else ever drops those keys, so an app that outlives
        # many deletes accumulates one dead generation and one dead lock per account.
        await forget_post_listener(account_id)
        # Same reason, other registry: ``_delete_account`` purges the account's
        # ``neurocomment_cooldowns`` rows so a re-imported id is not born parked, but the
        # live map those rows only back up is a service global core cannot reach.
        forget_account_cooldowns(account_id)
    await log_event("INFO", "account_removed", account_id=account_id)


async def evaluate_account_geo(account_id: str) -> GeoMatch:
    """Non-blocking geo check: does the account's proxy country match its number?

    Compares the phone number's country (via ``phonenumbers``) against the proxy
    exit country, plus the device language region. A mismatch is a warning + risk
    signal for the UI, never a hard block (product decision).
    """
    account = await fetch_account(account_id)
    if account is None:
        return GeoMatch(status="unknown", message="account not found")
    fingerprint = await fetch_device_fingerprint(account_id)
    lang_code = fingerprint.system_lang_code if fingerprint else None
    return evaluate_geo(
        phone=account.phone,
        proxy_country=account.proxy_country_code,
        lang_code=lang_code,
    )
