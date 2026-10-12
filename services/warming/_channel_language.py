"""Channel language matching — which language an account reads, and where the pool is short.

An account's language is its phone country's (``core.device_fingerprint_lang``), the
same one its Telegram client announces, so the channels it reads agree with what the
client says it speaks. A channel's language comes from the operator or from the post
text a warming read already fetched. Pure apart from the label write.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from core.config import settings
from core.db import observe_warming_channel
from core.device_fingerprint_lang import language_pair
from core.phone_geo import country_for_phone
from schemas.warming import ChannelLanguageShortage, WarmingChannelObservation
from services._lang_detect import detect_language

if TYPE_CHECKING:
    from collections.abc import Iterable

    from schemas.telegram_action_results import ReadChannelFacts
    from schemas.warming import WarmingChannel


def account_language(phone: str | None) -> str | None:
    """The phone country's language, ``None`` without a usable number.

    ``language_pair`` falls back to ``en`` for a missing number; here that would steer
    an unknown account onto English channels, so no country means no language.
    """
    if country_for_phone(phone) is None:
        return None
    return language_pair(phone)[0]


async def record_read_facts(channel: str, facts: ReadChannelFacts | None) -> None:
    """Label a channel from a read it just had; fills only what is still unknown."""
    if facts is None:
        return
    observed = WarmingChannelObservation(
        language=detect_language(facts.text_sample),
        kind=facts.kind,
        last_post_at=facts.last_post_at,
    )
    await observe_warming_channel(channel, observed)


def language_shortages(
    phones: Iterable[str | None],
    channels: list[WarmingChannel],
) -> list[ChannelLanguageShortage]:
    """Each account language with fewer own-language channels than the affinity floor.

    Sorted by how many accounts it leaves short, largest first, so the card leads with
    the language that hurts most.
    """
    floor = settings.warming.channel_affinity_min
    accounts = Counter(lang for phone in phones if (lang := account_language(phone)) is not None)
    owned = Counter(channel.language for channel in channels if channel.language is not None)
    shortages = [
        ChannelLanguageShortage(language=lang, channels=owned[lang], accounts=count)
        for lang, count in accounts.items()
        if owned[lang] < floor
    ]
    return sorted(shortages, key=lambda item: (-item.accounts, item.language))
