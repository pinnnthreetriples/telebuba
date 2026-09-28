"""Whether the neurocomment listener stands on an account a run would claim."""

from __future__ import annotations

from core.repositories.neurocomment import get_listener_account_id, get_listener_running


async def running_listener_account_id() -> str | None:
    """The account the neurocomment listener is subscribed with, or ``None``.

    A remembered-but-PAUSED listener answers ``None``, the same reading
    ``start_warming`` takes of the same two columns: the operator switched that runtime
    off, so the session is free until they switch it back on — and at that moment
    ``start_neurocomment`` is the half that refuses.

    Read from the database rather than from neurocomment's in-process owner because the
    listener is not a holder in ``services._account_owner`` (see that module's note on
    why), so these columns are the only record of it that survives a restart and that
    exists before neurocomment's own startup reconciliation has run.
    """
    if not await get_listener_running():
        return None
    return await get_listener_account_id()
