"""The pin rule: which of a campaign's accounts serve a given channel (pure, no I/O).

Its own module because all three "should this channel leave its campaign?" rules need it
(``bans``, ``_sweep``, ``_rejoin``) and the two that live under the sweep cannot import
``campaigns`` — that module reaches back through ``_runtime``, which is why they all
late-import it. One definition, so the three rules cannot drift apart again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas.neurocomment import CampaignAccountLink


def serving_accounts(links: list[CampaignAccountLink], channel: str) -> list[str]:
    """Accounts that may work ``channel``: unpinned serve every channel, pinned only their own."""
    return [link.account_id for link in links if not link.channels or channel in link.channels]
