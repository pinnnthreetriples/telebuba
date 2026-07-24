"""Data-access repository for the neurocomment domain (issue #114).

Split into submodules to stay within the file-size budget; this package is the
public surface. ``core.db`` re-exports these names so existing
``from core.db import …`` call sites keep working. Importing the package
registers the neurocomment tables in ``core.db._metadata`` (via ``_tables``).

Public functions wrap sync helpers via ``asyncio.to_thread`` and return Pydantic
models / ``None`` / ``bool`` — never raw rows (non-negotiable #2).
"""

from __future__ import annotations

from core.repositories.neurocomment._accounts import (
    ChannelNotInCampaignError,
    assign_account_to_campaign,
    list_campaign_accounts,
    remove_account_from_campaign,
    set_campaign_account_channels,
)
from core.repositories.neurocomment._bans import clear_pair_banned, mark_pair_banned
from core.repositories.neurocomment._campaigns import (
    ChannelAlreadyAssignedError,
    create_campaign,
    deactivate_channel,
    delete_campaign,
    fetch_active_campaign_for_channel,
    fetch_active_campaigns_for_channels,
    fetch_campaign,
    link_channel_to_campaign,
    list_active_watch_channels,
    list_campaign_channels,
    list_campaigns,
    set_campaign_status,
    update_campaign_prompt,
    update_solver_enabled,
)
from core.repositories.neurocomment._challenges import (
    count_by_outcome,
    evict_cached_decision,
    insert_challenge,
    list_challenged_channels,
    list_failed_for_channel,
    list_failed_for_channels,
    lookup_cached_decision,
    resolve_pending_outcome,
)
from core.repositories.neurocomment._comments import (
    claim_comment,
    delete_readiness,
    fetch_comment,
    fetch_linked_group,
    fetch_readiness,
    list_campaign_readiness,
    list_linked_groups,
    list_posted_comments_for_channel_since,
    list_posted_comments_page,
    list_posted_comments_since,
    mark_comment_failed,
    mark_comment_posted,
    mark_human_skipped,
    reclaim_stale_claims,
    upsert_linked_group,
    upsert_readiness,
)
from core.repositories.neurocomment._cooldowns import load_active_cooldowns, persist_cooldown
from core.repositories.neurocomment._deletions import mark_comments_deleted
from core.repositories.neurocomment._joins import count_account_joins_since, record_join
from core.repositories.neurocomment._quota import (
    count_account_channel_comments_since,
    count_account_comments_since,
    count_channel_comments_per_account_since,
    count_comments_per_account_since,
)
from core.repositories.neurocomment._runtime import (
    get_listener_account_id,
    get_listener_running,
    set_listener_account_id,
    set_listener_running,
)
from core.repositories.neurocomment._settings import (
    load_neurocomment_settings,
    save_neurocomment_settings,
)

__all__ = [
    "ChannelAlreadyAssignedError",
    "ChannelNotInCampaignError",
    "assign_account_to_campaign",
    "claim_comment",
    "clear_pair_banned",
    "count_account_channel_comments_since",
    "count_account_comments_since",
    "count_account_joins_since",
    "count_by_outcome",
    "count_channel_comments_per_account_since",
    "count_comments_per_account_since",
    "create_campaign",
    "deactivate_channel",
    "delete_campaign",
    "delete_readiness",
    "evict_cached_decision",
    "fetch_active_campaign_for_channel",
    "fetch_active_campaigns_for_channels",
    "fetch_campaign",
    "fetch_comment",
    "fetch_linked_group",
    "fetch_readiness",
    "get_listener_account_id",
    "get_listener_running",
    "insert_challenge",
    "link_channel_to_campaign",
    "list_active_watch_channels",
    "list_campaign_accounts",
    "list_campaign_channels",
    "list_campaign_readiness",
    "list_campaigns",
    "list_challenged_channels",
    "list_failed_for_channel",
    "list_failed_for_channels",
    "list_linked_groups",
    "list_posted_comments_for_channel_since",
    "list_posted_comments_page",
    "list_posted_comments_since",
    "load_active_cooldowns",
    "load_neurocomment_settings",
    "lookup_cached_decision",
    "mark_comment_failed",
    "mark_comment_posted",
    "mark_comments_deleted",
    "mark_human_skipped",
    "mark_pair_banned",
    "persist_cooldown",
    "reclaim_stale_claims",
    "record_join",
    "remove_account_from_campaign",
    "resolve_pending_outcome",
    "save_neurocomment_settings",
    "set_campaign_account_channels",
    "set_campaign_status",
    "set_listener_account_id",
    "set_listener_running",
    "update_campaign_prompt",
    "update_solver_enabled",
    "upsert_linked_group",
    "upsert_readiness",
]
