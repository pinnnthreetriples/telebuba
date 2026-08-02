"""The pin rule: which of a campaign's accounts serve a given channel (pure, no I/O).

Its own module because every "should this channel leave its campaign?" rule needs it
(``bans``, ``_sweep``, ``_rejoin``, ``_channel_pause``) and the ones that live under the
sweep cannot import ``campaigns`` — that module reaches back through ``_runtime``, which
is why they all late-import it. One definition, so the rules cannot drift apart again.

``engine._select_account`` reads it too, and is the reason a single definition is worth a
module: it decides who WORKS a channel, so an edit here that reached the drop rules but
not selection would leave the engine posting through accounts those rules had already
written off — or the other way round. Same list, one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas.neurocomment import CampaignAccountLink


def serving_accounts(links: list[CampaignAccountLink], channel: str) -> list[str]:
    """Accounts that may work ``channel``: unpinned serve every channel, pinned only their own."""
    return [link.account_id for link in links if not link.channels or channel in link.channels]
