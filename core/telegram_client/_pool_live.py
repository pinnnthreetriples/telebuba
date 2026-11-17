"""Whether the pool already holds a connected client for an account (no I/O, no connect).

Split out of ``_pool`` for its size. It reads the pool's own state through the module, so a flag
the pool reassigns (``_SHUTTING_DOWN``) is read as it is now, not as it was at import.
"""

# ruff: noqa: SLF001 - this module reads the pool's own state; that is its whole job.

from __future__ import annotations

from core.telegram_client import _pool


def has_live_client(account_id: str) -> bool:
    """Whether the pool already holds a connected, authorised client for the account.

    No I/O and no connect: it only looks at what is cached, so it can tell "this account is in a
    session right now" without making one (the spam gateway probes a live account at a moment of
    its session and never connects an idle one just to ask). The account's primary device when it
    is known, else any of its devices. ``is_connected()`` is synchronous; "authorised" is read
    from what the client already knows (``_authorized``, set by a sign-in or a successful
    request): only a client KNOWN to be unauthorised is refused, because asking would be I/O.
    """
    if _pool._SHUTTING_DOWN or _pool._is_removing(account_id):
        return False
    primary = _pool._PRIMARY.get(account_id)
    for device in _pool._DEVICE_TYPES if primary is None else (primary,):
        client = _pool._CLIENTS.get((account_id, device))
        if (
            client is not None
            and client.is_connected()
            and getattr(client, "_authorized", None) is not False
        ):
            return True
    return False
