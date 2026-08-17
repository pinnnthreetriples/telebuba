"""Data-access repository for the neuroshilling domain.

Split into submodules to stay within the file-size budget; this package is the
public surface. Unlike the older aggregates there is NO ``core.db`` re-export
block for it: that module is already close to its own ceiling, and every caller
here is new code that can import this package directly — which
``services.neurocomment`` and ``services.auth`` already do for theirs.

Importing the package registers the neuroshilling tables in ``core.db._metadata``
(via ``_tables``), which is what makes ``create_all`` build them.

Public functions wrap sync helpers via ``asyncio.to_thread`` and return Pydantic
models / ``None`` — never raw rows.
"""

from __future__ import annotations

from core.repositories.neuroshilling._accounts import (
    ReserveSwap,
    ban_campaign_account,
    count_substitutions,
    list_campaign_accounts,
    list_campaign_role_ids,
    substitute_banned_account,
)
from core.repositories.neuroshilling._campaigns import (
    create_campaign,
    delete_campaign,
    fetch_campaign,
    list_campaigns,
    list_live_campaigns,
    list_running_campaign_account_names,
    set_run_state,
    update_campaign,
)
from core.repositories.neuroshilling._chat_log import (
    chat_cursor,
    claim_chat_reply,
    count_chat_activity,
    count_chat_reply_usage,
    list_recent_chat,
    record_chat_messages,
    record_chat_reply,
)
from core.repositories.neuroshilling._message_counts import (
    count_messages_since,
    count_sent_message_steps,
    read_quota_usage,
)
from core.repositories.neuroshilling._messages import (
    claim_message,
    fail_pending_messages,
    fetch_message_id,
    hand_over_message,
    last_revive_cycle,
    list_journalled_steps,
    list_sent_message_ids,
    settle_message,
)
from core.repositories.neuroshilling._presence import (
    fetch_presence_state,
    list_halted_accounts,
    list_presence,
    record_presence,
    retire_account_presence,
)
from core.repositories.neuroshilling._scenario import (
    approve_scenario,
    load_scenario,
    replace_scenario,
)

__all__ = [
    "ReserveSwap",
    "approve_scenario",
    "ban_campaign_account",
    "chat_cursor",
    "claim_chat_reply",
    "claim_message",
    "count_chat_activity",
    "count_chat_reply_usage",
    "count_messages_since",
    "count_sent_message_steps",
    "count_substitutions",
    "create_campaign",
    "delete_campaign",
    "fail_pending_messages",
    "fetch_campaign",
    "fetch_message_id",
    "fetch_presence_state",
    "hand_over_message",
    "last_revive_cycle",
    "list_campaign_accounts",
    "list_campaign_role_ids",
    "list_campaigns",
    "list_halted_accounts",
    "list_journalled_steps",
    "list_live_campaigns",
    "list_presence",
    "list_recent_chat",
    "list_running_campaign_account_names",
    "list_sent_message_ids",
    "load_scenario",
    "read_quota_usage",
    "record_chat_messages",
    "record_chat_reply",
    "record_presence",
    "replace_scenario",
    "retire_account_presence",
    "set_run_state",
    "settle_message",
    "substitute_banned_account",
    "update_campaign",
]
