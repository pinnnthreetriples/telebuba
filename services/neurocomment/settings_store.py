"""Neurocomment settings — load/save the operator-editable limits (#19).

Thin orchestration over ``core.db``. The engine reads :func:`load_settings` at
account selection / pacing, so a saved override takes effect on the next post;
with no saved row the store returns live ``settings.neurocomment`` config.

Named ``settings_store`` (not ``settings``) to avoid shadowing the ``settings``
object from ``core.config`` in the package namespace — mirrors warming.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.db import load_neurocomment_settings, save_neurocomment_settings
from core.logging import log_event

if TYPE_CHECKING:
    from schemas.neurocomment import NeurocommentSettings, NeurocommentSettingsUpdate


async def load_settings() -> NeurocommentSettings:
    """Effective neurocomment limits — saved override, else live config."""
    return await load_neurocomment_settings()


async def save_settings(data: NeurocommentSettingsUpdate) -> NeurocommentSettings:
    """Persist the operator's neurocomment-settings override."""
    saved = await save_neurocomment_settings(data)
    await log_event(
        "INFO",
        "neurocomment_settings_saved",
        extra={
            "max_comments_per_hour": saved.max_comments_per_hour,
            "max_comments_per_channel_per_day": saved.max_comments_per_channel_per_day,
            "min_trust_score": saved.min_trust_score,
        },
    )
    return saved
