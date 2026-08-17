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
    list_campaign_accounts,
    list_campaign_role_ids,
)
from core.repositories.neuroshilling._campaigns import (
    create_campaign,
    delete_campaign,
    fetch_campaign,
    list_campaigns,
    list_running_campaign_account_names,
    update_campaign,
)
from core.repositories.neuroshilling._scenario import (
    approve_scenario,
    load_scenario,
    replace_scenario,
)

__all__ = [
    "approve_scenario",
    "create_campaign",
    "delete_campaign",
    "fetch_campaign",
    "list_campaign_accounts",
    "list_campaign_role_ids",
    "list_campaigns",
    "list_running_campaign_account_names",
    "load_scenario",
    "replace_scenario",
    "update_campaign",
]
