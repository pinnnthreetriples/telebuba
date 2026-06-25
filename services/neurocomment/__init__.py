"""Neurocomment engine (package) — campaign comment automation.

Pure business logic (non-negotiable #11): onboarding prepares accounts to
comment (issue #117); the engine reacts to fresh posts by generating + posting a
short comment (#118). Reaches Telegram/Gemini/spam only through the gateway and
the DB only through ``core.repositories.neurocomment``, all behind ``_seams`` so
tests patch one place. NiceGUI wiring (#119) delegates here.
"""

from __future__ import annotations

from services.neurocomment._runtime import (
    neurocomment_runtime_status,
    on_post,
    reconcile_neurocomment_on_startup,
    reconcile_neurocomment_runtime,
    shutdown_neurocomment_on_shutdown,
    shutdown_neurocomment_runtime,
    start_neurocomment,
    stop_neurocomment,
)
from services.neurocomment.board import load_neurocomment_board
from services.neurocomment.campaigns import (
    assign_account_to_campaign,
    count_challenge_outcomes,
    create_campaign,
    deactivate_channel,
    delete_campaign,
    link_channel,
    list_campaign_accounts,
    list_campaign_channels,
    list_campaigns,
    list_channel_challenges,
    remove_account_from_campaign,
    set_solver_enabled,
    skip_pair,
)
from services.neurocomment.challenge import retry_pair
from services.neurocomment.engine import handle_new_post
from services.neurocomment.onboarding import onboard_account_channel, onboard_campaign

__all__ = [
    "assign_account_to_campaign",
    "count_challenge_outcomes",
    "create_campaign",
    "deactivate_channel",
    "delete_campaign",
    "handle_new_post",
    "link_channel",
    "list_campaign_accounts",
    "list_campaign_channels",
    "list_campaigns",
    "list_channel_challenges",
    "load_neurocomment_board",
    "neurocomment_runtime_status",
    "on_post",
    "onboard_account_channel",
    "onboard_campaign",
    "reconcile_neurocomment_on_startup",
    "reconcile_neurocomment_runtime",
    "remove_account_from_campaign",
    "retry_pair",
    "set_solver_enabled",
    "shutdown_neurocomment_on_shutdown",
    "shutdown_neurocomment_runtime",
    "skip_pair",
    "start_neurocomment",
    "stop_neurocomment",
]
