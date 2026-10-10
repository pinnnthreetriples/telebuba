"""Data-access repository for the masstagging domain.

The public surface; importing it registers the tables in ``core.db._metadata`` (via
``_tables``). Public functions wrap sync helpers via ``asyncio.to_thread`` and return
Pydantic models — never raw rows.
"""

from __future__ import annotations

from core.repositories.masstagging._campaigns import (
    create_campaign,
    delete_campaign,
    fetch_campaign,
    list_campaigns,
    list_live_campaigns,
    list_roster,
    list_running_account_names,
    save_pace,
    save_settings,
    set_runtime,
    set_status,
)
from core.repositories.masstagging._days import bump_day, fetch_days, first_days, set_day
from core.repositories.masstagging._targets import (
    add_events,
    add_targets,
    count_tagged_since,
    fetch_target,
    list_journal,
    list_targets,
    merge_targets,
    register_tagged,
    replace_targets,
    tagged_user_ids,
    update_target,
    update_targets,
)

__all__ = [
    "add_events",
    "add_targets",
    "bump_day",
    "count_tagged_since",
    "create_campaign",
    "delete_campaign",
    "fetch_campaign",
    "fetch_days",
    "fetch_target",
    "first_days",
    "list_campaigns",
    "list_journal",
    "list_live_campaigns",
    "list_roster",
    "list_running_account_names",
    "list_targets",
    "merge_targets",
    "register_tagged",
    "replace_targets",
    "save_pace",
    "save_settings",
    "set_day",
    "set_runtime",
    "set_status",
    "tagged_user_ids",
    "update_target",
    "update_targets",
]
