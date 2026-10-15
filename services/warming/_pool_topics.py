"""What a search with no topics looks for: the interests of that language's accounts.

The automatic refill, and «Найти каналы» left without topics, search for what the
accounts of the language are into — the interests of their characters (4.2), most
shared first — so the pool grows towards the people who will read it. A language with
no character yet falls back to a few everyday subjects people of any country follow.
"""

from __future__ import annotations

from collections import Counter
from typing import Final

from core.repositories.warming_character import list_account_characters

_MAX_TOPICS: Final = 5
_EVERYDAY: Final[dict[str, tuple[str, ...]]] = {
    "es": ("noticias", "fútbol", "recetas", "música", "cine"),
    "pt": ("notícias", "futebol", "receitas", "música", "filmes"),
    "en": ("news", "football", "recipes", "music", "movies"),
    "ru": ("новости", "футбол", "рецепты", "музыка", "кино"),
    "uk": ("новини", "футбол", "рецепти", "музика", "кіно"),
    "fr": ("actualités", "football", "recettes", "musique", "cinéma"),
    "de": ("nachrichten", "fußball", "rezepte", "musik", "filme"),
    "it": ("notizie", "calcio", "ricette", "musica", "cinema"),
    "tr": ("haberler", "futbol", "tarifler", "müzik", "sinema"),
}


def everyday_topics(language: str) -> list[str]:
    """The fallback subjects for ``language``; empty for a language with none listed."""
    return list(_EVERYDAY.get(language, ()))


async def topics_for(language: str) -> list[str]:
    """Up to five search topics for ``language``; empty when nothing is known for it."""
    counts = Counter(
        interest
        for character in await list_account_characters()
        if character.language == language
        for interest in character.interests
    )
    topics = [topic for topic, _count in counts.most_common(_MAX_TOPICS)]
    for topic in everyday_topics(language):
        if len(topics) >= _MAX_TOPICS:
            break
        if topic not in topics:
            topics.append(topic)
    return topics
