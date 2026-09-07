"""Read-only extras runners — the free, "what a client does on open" half of the step.

Each runner builds one ``Warm*`` action and dispatches it; reads book no budget.
Randomness goes through ``_seams.rng`` like everywhere else in warming so tests pin
one generator. Nothing here inspects what Telegram returned: warming only cares
that the request looked like a real client's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from schemas.telegram_actions_warming import (
    WarmBrowseStickers,
    WarmCheckSettings,
    WarmGetDialogs,
    WarmInlineQuery,
    WarmLinkPreview,
    WarmReadContacts,
    WarmReadNotifySettings,
    WarmSavedGifs,
    WarmSearchMessages,
    WarmViewProfile,
)
from services.warming import _seams
from services.warming._steps import _human_pause

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult
    from services.warming._extras_ctx import _ExtraContext

# A chat-list page as the mobile client pages it (20 on first open, more on scroll).
_DIALOGS_LIMIT = (20, 40)
# How many settings screens one "check my settings" glance opens.
_CHECK_SETTINGS_CALLS = (2, 4)
# Which screen it opens first: one of the 11 reads in core's table (schema ``le=10``).
_CHECK_SETTINGS_OFFSETS = 11
# Whom one profile glance opens: a quarter of the time an official inline bot, half a
# channel we just read (our own profile when none was), the rest our own profile.
_BOT_PROFILE_PROBABILITY = 0.25
_CHANNEL_PROFILE_PROBABILITY = 0.5
# The two "look at posts just read" actions take at most this many ids (schema cap).
_POST_IDS_MAX = 5


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
    draw = _seams.rng.random()
    if draw < _BOT_PROFILE_PROBABILITY:
        action = WarmViewProfile(
            kind="bot", bot=_seams.rng.choice(settings.warming.extras_inline_bots)
        )
    elif draw < _BOT_PROFILE_PROBABILITY + _CHANNEL_PROFILE_PROBABILITY and ctx.chosen:
        action = WarmViewProfile(kind="channel", channel=_seams.rng.choice(ctx.chosen).channel)
    else:
        action = WarmViewProfile(kind="self")
    return await _seams.execute(ctx.account_id, action)


def _recent_posts(ctx: _ExtraContext) -> tuple[str, list[int]]:
    """A channel whose read fetched posts, plus up to five of them (``needs`` guarantees one)."""
    channel = _seams.rng.choice([c for c, ids in ctx.recent_ids.items() if ids])
    return channel, ctx.recent_ids[channel][:_POST_IDS_MAX]


def _query() -> str:
    return _seams.rng.choice(settings.warming.extras_search_queries)


async def search_messages(ctx: _ExtraContext) -> ActionResult:
    channel, ids = _recent_posts(ctx)
    action = WarmSearchMessages(
        channel=channel,
        message_ids=ids,
        fallback_query=_query(),
        global_search=_seams.rng.random() < settings.warming.extras_global_search_probability,
    )
    return await _seams.execute(ctx.account_id, action)


async def link_preview(ctx: _ExtraContext) -> ActionResult:
    channel, ids = _recent_posts(ctx)
    return await _seams.execute(ctx.account_id, WarmLinkPreview(channel=channel, message_ids=ids))


async def stickers(ctx: _ExtraContext) -> ActionResult:
    return await _seams.execute(ctx.account_id, WarmBrowseStickers())


async def gif(ctx: _ExtraContext) -> ActionResult:
    """Open the GIF tab, then type into it — two RPCs, one extra; a failed open stops here."""
    result = await _seams.execute(ctx.account_id, WarmSavedGifs())
    if result.status != "ok":
        return result
    warm = settings.warming
    await _human_pause(warm.action_delay_min_seconds, warm.action_delay_max_seconds)
    return await _seams.execute(ctx.account_id, WarmInlineQuery(bot="gif", query=_query()))


async def inline_bots(ctx: _ExtraContext) -> ActionResult:
    bot = _seams.rng.choice(settings.warming.extras_inline_bots)
    return await _seams.execute(ctx.account_id, WarmInlineQuery(bot=bot, query=_query()))
