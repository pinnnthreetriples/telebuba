"""Neurocomment engine (package) — campaign comment automation.

Pure business logic (non-negotiable #11): the onboarding flow that prepares
accounts to comment ahead of a fresh post. Reaches Telegram only through the
gateway and the DB only through ``core.repositories.neurocomment``, both behind
``_seams`` so tests patch one place. NiceGUI handlers (future) delegate here.

Issue #117 ships onboarding only; the comment listener (#118/#119) lands later.
"""

from __future__ import annotations

from services.neurocomment.onboarding import onboard_account_channel, onboard_campaign

__all__ = ["onboard_account_channel", "onboard_campaign"]
