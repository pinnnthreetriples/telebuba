"""Who each warming account is — made once, then kept (Прогрев 2.0, item 4.2).

Code picks what must follow the phone: the language, the country and a city in it,
plus an age and a gender, all from the fleet's salted hash so they never drift. The
text model fills only what it is good at — an occupation and 3-5 interests — in JSON
mode, in the account's language. Anti-repeat across the fleet: no two accounts share
an occupation, and interests overlap (Jaccard) stays under
``character_interests_overlap_max``; a profile that fails is asked again.

The model is reached through :mod:`services.warming._seams` like every other line
warming writes, so tests patch it in one place.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account
from core.logging import log_event
from core.phone_geo import country_for_phone
from core.repositories.warming_character import (
    fetch_account_character,
    list_account_characters,
    store_account_character,
)
from schemas._warming_character import AccountCharacter
from services._account_language import account_language
from services._text_llm import text_llm
from services.warming import _seams
from services.warming._character_data import (
    CITIES_BY_COUNTRY,
    FALLBACK_LANGUAGE,
    LANGUAGE_NAMES,
)
from services.warming._fleet import _stable_fraction

if TYPE_CHECKING:
    from schemas.warming import WarmingSettingsSecret

_MIN_OCCUPATION = 2
_MAX_OCCUPATION = 60
_FEMALE_SHARE = 0.5
_MAX_INTEREST = 40
_INTERESTS = (3, 5)
_PROFILE_PROMPT = (
    "Invent a believable, ordinary person for a messenger profile. They are {age}, "
    "{gender}, and live in {place}. Answer with JSON only, exactly "
    '{{"occupation": "...", "interests": ["...", "..."]}}: "occupation" is a common '
    'everyday job written in {language}, none of these: {taken}; "interests" are 3 to 5 '
    "short everyday hobbies written in {language}, lowercase, no emoji."
)


def language_name(language: str) -> str:
    """The language's English name for a prompt; an unlisted code is passed as is."""
    return LANGUAGE_NAMES.get(language, language)


def base_character(account_id: str, phone: str | None) -> AccountCharacter:
    """The parts code decides, with placeholders for what the model fills."""
    lo, hi = settings.warming.character_age_range
    country = country_for_phone(phone)
    cities = CITIES_BY_COUNTRY.get(country or "", ())
    city_index = int(_stable_fraction(f"city:{account_id}") * len(cities)) if cities else 0
    return AccountCharacter(
        account_id=account_id,
        language=account_language(phone) or FALLBACK_LANGUAGE,
        country=country,
        age=lo + int(_stable_fraction(f"age:{account_id}") * (hi - lo + 1)),
        gender="female" if _stable_fraction(f"gender:{account_id}") < _FEMALE_SHARE else "male",
        city=cities[city_index] if cities else None,
        occupation="--",
        interests=["-", "-", "-"],
    )


def _parse_profile(raw: str | None) -> tuple[str, list[str]] | None:
    """``(occupation, interests)`` from the model's JSON, ``None`` when unusable."""
    try:
        body = json.loads(raw or "")
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    occupation = body.get("occupation")
    interests = body.get("interests")
    if not isinstance(occupation, str) or not isinstance(interests, list):
        return None
    occupation = occupation.strip()
    cleaned: list[str] = []
    for item in interests:
        text = item.strip().lower() if isinstance(item, str) else ""
        if text and len(text) <= _MAX_INTEREST and text not in cleaned:
            cleaned.append(text)
    lo, hi = _INTERESTS
    if not _MIN_OCCUPATION <= len(occupation) <= _MAX_OCCUPATION or len(cleaned) < lo:
        return None
    return occupation, cleaned[:hi]


def interests_overlap(a: list[str], b: list[str]) -> float:
    """Jaccard overlap of two interest lists, case-insensitive."""
    left, right = {x.casefold() for x in a}, {x.casefold() for x in b}
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def is_distinct(candidate: AccountCharacter, others: list[AccountCharacter]) -> bool:
    """Unique occupation and low interest overlap against every other account."""
    limit = settings.warming.character_interests_overlap_max
    occupation = candidate.occupation.casefold()
    return all(
        other.occupation.casefold() != occupation
        and interests_overlap(candidate.interests, other.interests) < limit
        for other in others
    )


async def _ask_profile(
    base: AccountCharacter,
    others: list[AccountCharacter],
    secret: WarmingSettingsSecret,
) -> tuple[str, list[str]] | None:
    place = ", ".join(part for part in (base.city, base.country) if part) or "their country"
    prompt = _PROFILE_PROMPT.format(
        age=base.age,
        gender=base.gender,
        place=place,
        language=language_name(base.language),
        taken=", ".join(sorted({o.occupation for o in others})) or "-",
    )
    llm = text_llm(secret)
    generate = _seams.generate_text_deepseek if llm.use_deepseek else _seams.generate_text
    result = await generate(llm.request(prompt, response_json_object=True))
    return _parse_profile(result.text) if result.status == "ok" else None


async def account_character(
    account_id: str,
    secret: WarmingSettingsSecret,
) -> AccountCharacter | None:
    """The account's character, made on first need; ``None`` when off or not made yet.

    A profile the model could not make distinct is not stored, so the next line asks
    again; until then the account writes in its language without a persona. A stored
    character whose language no longer matches the phone (the session check learnt a
    new number) is made again, so the account never chats in its old language.
    """
    if not settings.warming.character_enabled:
        return None
    stored = await fetch_account_character(account_id)
    account = await fetch_account(account_id)
    phone = account.phone if account else None
    current = account_language(phone)
    if stored is not None and (current is None or stored.language == current):
        return stored
    base = base_character(account_id, phone)
    others = [c for c in await list_account_characters() if c.account_id != account_id]
    for _ in range(settings.warming.character_max_attempts):
        profile = await _ask_profile(base, others, secret)
        if profile is None:
            continue
        candidate = base.model_copy(update={"occupation": profile[0], "interests": profile[1]})
        if is_distinct(candidate, others):
            await store_account_character(candidate, replace=stored is not None)
            await log_event("INFO", "warming_character_created", account_id=account_id)
            return candidate
    await log_event("WARNING", "warming_character_failed", account_id=account_id)
    return None
