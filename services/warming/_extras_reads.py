"""Read-only extras runners — the free, "what a client does on open" half of the step.

Each runner builds one ``Warm*`` action and dispatches it; reads book no budget.
Randomness goes through ``_seams.rng`` like everywhere else in warming so tests pin
one generator. Nothing here inspects what Telegram returned: warming only cares
that the request looked like a real client's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from schemas.telegram_actions_warming import (
    WarmCheckSettings,
    WarmGetDialogs,
    WarmReadContacts,
    WarmReadNotifySettings,
    WarmViewProfile,
)
from services.warming import _seams

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult
    from services.warming._extras_ctx import _ExtraContext

# A chat-list page as the mobile client pages it (20 on first open, more on scroll).
_DIALOGS_LIMIT = (20, 40)
# How many settings screens one "check my settings" glance opens.
_CHECK_SETTINGS_CALLS = (2, 4)
# Which screen it opens first: one of the 11 reads in core's table (schema ``le=10``).
_CHECK_SETTINGS_OFFSETS = 11
# Half the time look at a channel we just read, else at our own profile.
_CHANNEL_PROFILE_PROBABILITY = 0.5


async def dialogs(ctx: _ExtraContext) -> ActionResult:
    return await _seams.execute(
        ctx.account_id, WarmGetDialogs(limit=_seams.rng.randint(*_DIALOGS_LIMIT))
    )


async def contacts(ctx: _ExtraContext) -> ActionResult:
    return await _seams.execute(ctx.account_id, WarmReadContacts())


async def notifications(ctx: _ExtraContext) -> ActionResult:
    return await _seams.execute(ctx.account_id, WarmReadNotifySettings())


async def check_settings(ctx: _ExtraContext) -> ActionResult:
    action = WarmCheckSettings(
        calls=_seams.rng.randint(*_CHECK_SETTINGS_CALLS),
        offset=_seams.rng.randrange(_CHECK_SETTINGS_OFFSETS),
    )
    return await _seams.execute(ctx.account_id, action)


async def view_profiles(ctx: _ExtraContext) -> ActionResult:
    if ctx.chosen and _seams.rng.random() < _CHANNEL_PROFILE_PROBABILITY:
        action = WarmViewProfile(kind="channel", channel=_seams.rng.choice(ctx.chosen).channel)
    else:
        action = WarmViewProfile(kind="self")
    return await _seams.execute(ctx.account_id, action)
