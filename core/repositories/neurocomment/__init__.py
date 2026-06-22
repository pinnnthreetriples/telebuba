"""Data-access repository for the neurocomment domain (issue #114).

Split into submodules to stay within the file-size budget; this package is the
public surface. ``core.db`` re-exports these names so existing
``from core.db import …`` call sites keep working. Importing the package
registers the neurocomment tables in ``core.db._metadata`` (via ``_tables``).

Public functions wrap sync helpers via ``asyncio.to_thread`` and return Pydantic
models / ``None`` / ``bool`` — never raw rows (non-negotiable #2).
"""

from __future__ import annotations

from core.repositories.neurocomment._campaigns import (
    ChannelAlreadyAssignedError,
    assign_account_to_campaign,
    create_campaign,
    deactivate_channel,
    fetch_campaign,
    link_channel_to_campaign,
    list_campaign_accounts,
    list_campaign_channels,
    list_campaigns,
)
from core.repositories.neurocomment._comments import (
    claim_comment,
    fetch_comment,
    fetch_linked_group,
    fetch_readiness,
    mark_comment_failed,
    mark_comment_posted,
    upsert_linked_group,
    upsert_readiness,
)

__all__ = [
    "ChannelAlreadyAssignedError",
    "assign_account_to_campaign",
    "claim_comment",
    "create_campaign",
    "deactivate_channel",
    "fetch_campaign",
    "fetch_comment",
    "fetch_linked_group",
    "fetch_readiness",
    "link_channel_to_campaign",
    "list_campaign_accounts",
    "list_campaign_channels",
    "list_campaigns",
    "mark_comment_failed",
    "mark_comment_posted",
    "upsert_linked_group",
    "upsert_readiness",
]
