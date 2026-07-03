"""Account deletion (split out of ``core.repositories.accounts`` for size).

Owns the cascade-delete of an account and every per-account child row. Kept in
its own module so ``core.repositories.accounts`` stays under the file-size
budget; ``delete_account`` is re-imported there and re-exported by ``core.db``,
so existing call sites are unaffected.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, update

from core.db import (
    _accounts,
    _device_fingerprints,
    _get_engine,
    _now_iso,
    _warming_joined_channels,
)


def _delete_account(account_id: str) -> None:
    # F4: schema declares ForeignKey on warming_account_state /
    # account_spam_status without ON DELETE CASCADE, and PRAGMA foreign_keys=ON,
    # so deleting a warmed account explodes with IntegrityError unless we clean
    # the children first. device_fingerprints / dialogue tables have no FK but
    # are still per-account data that must not outlive the account. The proxy is
    # a shared pool row (accounts.proxy_id → proxies.id) — it is NOT a child and
    # must outlive the account, so it is left untouched here.
    from core.db import _account_spam_status, _warming_account_state  # noqa: PLC0415
    from core.repositories.dialogues import dialogue_messages, dialogue_pairs  # noqa: PLC0415
    from core.repositories.neurocomment._tables import (  # noqa: PLC0415
        _neurocomment_campaign_accounts,
        _neurocomment_challenges,
        _neurocomment_comments,
        _neurocomment_readiness,
        _neurocomment_runtime,
    )

    with _get_engine().begin() as connection:
        # Neurocomment children FK accounts.account_id (campaign serving links,
        # per-channel readiness, posted/claimed comments) → clear them first too.
        connection.execute(
            delete(_neurocomment_campaign_accounts).where(
                _neurocomment_campaign_accounts.c.account_id == account_id,
            ),
        )
        connection.execute(
            delete(_neurocomment_readiness).where(
                _neurocomment_readiness.c.account_id == account_id,
            ),
        )
        connection.execute(
            delete(_neurocomment_comments).where(
                _neurocomment_comments.c.account_id == account_id,
            ),
        )
        # audit #1: neurocomment_challenges carries account_id but no FK, so
        # orphan give-up/challenge rows would keep the channel flagged
        # "challenged" on the board forever (count/list-by-outcome scan by
        # channel). Purge the deleted account's rows in the same transaction.
        connection.execute(
            delete(_neurocomment_challenges).where(
                _neurocomment_challenges.c.account_id == account_id,
            ),
        )
        # If this account was the persisted listener, clear the pointer AND the
        # run flag so reconcile_neurocomment_on_startup does not re-point at a
        # ghost (and a paused-listener row can't resume onto a deleted account).
        connection.execute(
            update(_neurocomment_runtime)
            .where(_neurocomment_runtime.c.listener_account_id == account_id)
            .values(listener_account_id=None, listener_running=False, updated_at=_now_iso()),
        )
        connection.execute(
            delete(_warming_joined_channels).where(
                _warming_joined_channels.c.account_id == account_id,
            ),
        )
        connection.execute(
            delete(_warming_account_state).where(
                _warming_account_state.c.account_id == account_id,
            ),
        )
        connection.execute(
            delete(_account_spam_status).where(
                _account_spam_status.c.account_id == account_id,
            ),
        )
        connection.execute(
            delete(_device_fingerprints).where(
                _device_fingerprints.c.account_id == account_id,
            ),
        )
        connection.execute(
            delete(dialogue_messages).where(
                (dialogue_messages.c.from_account == account_id)
                | (dialogue_messages.c.to_account == account_id),
            ),
        )
        connection.execute(
            delete(dialogue_pairs).where(
                (dialogue_pairs.c.account_a == account_id)
                | (dialogue_pairs.c.account_b == account_id),
            ),
        )
        connection.execute(delete(_accounts).where(_accounts.c.account_id == account_id))


async def delete_account(account_id: str) -> None:
    """Delete an account row + every per-account child row.

    SQLite FKs are declared without ``ON DELETE CASCADE`` (see F4); this
    helper manually purges ``warming_account_state`` /
    ``account_spam_status`` / ``device_fingerprints`` / dialogue tables /
    joined channels before deleting the ``accounts`` row. The shared pool
    proxy is left intact. New per-account tables MUST be added to
    ``_delete_account`` — relying on FK cascade is a bug.

    Does not stop a running warming task. Service callers should use
    :func:`services.accounts.lifecycle.remove_account` instead, which holds
    the per-account runtime lock across stop + delete (P2.2). The
    ``_tdata`` rollback path is the only legitimate direct caller of this
    repo function because those accounts never started warming.
    """
    await asyncio.to_thread(_delete_account, account_id)
