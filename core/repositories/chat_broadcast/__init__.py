"""Data-access repository for the chat-broadcast domain.

The public surface; importing it registers the tables in ``core.db._metadata`` (via
``_tables``). Public functions wrap sync helpers via ``asyncio.to_thread`` and return
Pydantic models — never raw rows.
"""

from __future__ import annotations

from core.repositories.chat_broadcast._campaigns import (
    create_campaign,
    delete_campaign,
    fetch_campaign,
    list_campaigns,
    list_live_campaigns,
    list_running_account_names,
    save_settings,
    set_runtime,
    set_status,
)
from core.repositories.chat_broadcast._journal import (
    add_event,
    claim_message,
    count_account_sends_since,
    count_run_sent,
    list_events,
    list_journal,
    mark_deleted,
    settle_message,
    unconfirm_pending,
    written_before,
)
from core.repositories.chat_broadcast._targets import (
    fetch_target,
    finish_rounds,
    halt_account,
    list_roster,
    list_targets,
    merge_targets,
    replace_targets,
    set_consecutive_errors,
    settle_interrupted,
    start_round,
    update_target,
)

__all__ = [
    "add_event",
    "claim_message",
    "count_account_sends_since",
    "count_run_sent",
    "create_campaign",
    "delete_campaign",
    "fetch_campaign",
    "fetch_target",
    "finish_rounds",
    "halt_account",
    "list_campaigns",
    "list_events",
    "list_journal",
    "list_live_campaigns",
    "list_roster",
    "list_running_account_names",
    "list_targets",
    "mark_deleted",
    "merge_targets",
    "replace_targets",
    "save_settings",
    "set_consecutive_errors",
    "set_runtime",
    "set_status",
    "settle_interrupted",
    "settle_message",
    "start_round",
    "unconfirm_pending",
    "update_target",
    "written_before",
]
