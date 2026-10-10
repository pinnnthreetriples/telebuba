"""Masstagging («Масстегинг»): story mentions and priming through the auto-delete toggle.

The package is the public surface — re-exports only.
"""

from __future__ import annotations

from services.masstagging._errors import (
    MasstaggingConflictError,
    MasstaggingInvalidError,
    MasstaggingRefusedError,
)
from services.masstagging._runtime import (
    reconcile_masstagging_on_startup,
    shutdown_masstagging_on_shutdown,
    start_campaign,
    stop_campaign,
)
from services.masstagging.actions import act_on_target
from services.masstagging.board import load_board
from services.masstagging.campaigns import (
    campaign_exists,
    create_campaign,
    delete_campaign,
    list_campaigns,
    load_settings,
    save_settings,
)
from services.masstagging.gear import save_pace
from services.masstagging.media import max_bytes, upload_media
from services.masstagging.targets import from_base, import_file

__all__ = [
    "MasstaggingConflictError",
    "MasstaggingInvalidError",
    "MasstaggingRefusedError",
    "act_on_target",
    "campaign_exists",
    "create_campaign",
    "delete_campaign",
    "from_base",
    "import_file",
    "list_campaigns",
    "load_board",
    "load_settings",
    "max_bytes",
    "reconcile_masstagging_on_startup",
    "save_pace",
    "save_settings",
    "shutdown_masstagging_on_shutdown",
    "start_campaign",
    "stop_campaign",
    "upload_media",
]
