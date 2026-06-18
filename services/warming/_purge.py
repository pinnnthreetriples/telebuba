"""Best-effort retention sweep over warming's append-only tables.

Extracted from ``_runtime`` (Round-4) so the runtime module stays under the
aislop file-size gate; the function itself is unchanged. Invoked once per
``reconcile_warming_runtime`` call.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.config import settings
from core.db import (
    purge_dialogue_messages_older_than,
    purge_logs_older_than,
    purge_sent_hashes_older_than,
)
from core.logging import log_event


async def purge_stale_history() -> None:
    """Best-effort retention pass on append-only tables (logs / dialogues / hashes).

    Each window comes from ``settings.warming``; setting a window to 0 disables
    the corresponding purge. Failures are logged and swallowed — retention is
    nice-to-have, never a reason to abort reconcile.
    """
    now = datetime.now(UTC)
    plans = [
        (
            settings.warming.log_retention_days,
            "log_retention_purged",
            purge_logs_older_than,
        ),
        (
            settings.warming.dialogue_message_retention_days,
            "dialogue_message_retention_purged",
            purge_dialogue_messages_older_than,
        ),
        (
            settings.warming.sent_hash_retention_days,
            "sent_hash_retention_purged",
            purge_sent_hashes_older_than,
        ),
    ]
    for window_days, event, purge in plans:
        if window_days <= 0:
            continue
        cutoff = (now - timedelta(days=window_days)).isoformat()
        try:
            removed = await purge(cutoff)
        except Exception as exc:  # noqa: BLE001 - retention failures must not block reconcile.
            await log_event(
                "WARNING",
                "retention_purge_failed",
                extra={"event": event, "error": str(exc)},
            )
            continue
        if removed:
            await log_event("INFO", event, extra={"removed": removed})
