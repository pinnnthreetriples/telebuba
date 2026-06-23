"""Neurocomment engine (package) — campaign comment automation.

Pure business logic (non-negotiable #11): onboarding prepares accounts to
comment (issue #117); the engine reacts to fresh posts by generating + posting a
short comment (#118). Reaches Telegram/Gemini/spam only through the gateway and
the DB only through ``core.repositories.neurocomment``, all behind ``_seams`` so
tests patch one place. NiceGUI wiring (#119) delegates here.
"""

from __future__ import annotations

from services.neurocomment._runtime import (
    on_post,
    reconcile_neurocomment_runtime,
    shutdown_neurocomment_runtime,
)
from services.neurocomment.engine import handle_new_post
from services.neurocomment.onboarding import onboard_account_channel, onboard_campaign

__all__ = [
    "handle_new_post",
    "on_post",
    "onboard_account_channel",
    "onboard_campaign",
    "reconcile_neurocomment_runtime",
    "shutdown_neurocomment_runtime",
]
