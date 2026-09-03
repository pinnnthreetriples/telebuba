"""Category word bundles for channel discovery.

A category is a canned keyword set the operator picks instead of (or on top of) typing
keywords: ``keywords_for`` feeds the search sweep, ``matches`` decides at qualification
whether a found channel's title + about actually read as that topic. Every word is at
least ``KEYWORD_MIN_LENGTH`` characters so a bundle can ride the same request validator
as typed keywords. Keyed by every ``DiscoveryCategory`` code except ``any`` — a test
holds the two sets equal.
"""

from __future__ import annotations

BUNDLES: dict[str, tuple[str, ...]] = {
    "it_programming": (
        "программирование",
        "разработка",
        "python",
        "javascript",
        "devops",
        "айти",
        "coding",
        "frontend",
    ),
    "beauty_health": (
        "красота",
        "здоровье",
        "уход",
        "косметика",
        "фитнес",
        "wellness",
        "beauty",
        "диета",
    ),
    "crypto": (
        "крипта",
        "криптовалюта",
        "биткоин",
        "bitcoin",
        "crypto",
        "альткоины",
        "defi",
        "блокчейн",
    ),
    "trading": ("трейдинг", "трейдер", "трейдинг сигналы", "trading", "forex", "биржа", "акции"),
    "news": ("новости", "news", "события", "breaking", "сводка", "происшествия"),
    "business": ("бизнес", "business", "предприниматель", "стартап", "startup", "инвестиции"),
    "marketing": ("маркетинг", "marketing", "реклама", "таргет", "продвижение", "соцсети"),
    "education": ("образование", "обучение", "курсы", "education", "учеба", "лекции"),
    "entertainment": ("развлечения", "entertainment", "ток-шоу", "юмор", "видео", "тренды"),
    "games": ("игры", "games", "gaming", "геймер", "киберспорт", "esports"),
    "sport": ("спорт", "sport", "футбол", "хоккей", "fitness", "тренировки"),
    "travel": ("путешествия", "travel", "туризм", "отдых", "туры", "авиабилеты"),
    "food": ("рецепты", "кулинария", "food", "cooking", "вкусно", "ресторан"),
    "cars": ("авто", "автомобили", "cars", "машины", "автоновости", "тюнинг"),
    "real_estate": ("недвижимость", "квартиры", "real estate", "ипотека", "новостройки", "аренда"),
    "finance": ("финансы", "finance", "деньги", "банки", "экономика", "инвестиции"),
    "psychology": ("психология", "psychology", "психолог", "отношения", "саморазвитие", "mindset"),
    "humor": ("юмор", "humor", "мемы", "memes", "приколы", "анекдоты"),
    "music": ("музыка", "music", "треки", "плейлист", "концерты", "альбом"),
    "movies": ("кино", "фильмы", "movies", "сериалы", "cinema", "трейлер"),
    "fashion": ("мода", "fashion", "стиль", "одежда", "тренды", "образы"),
    "politics": ("политика", "politics", "выборы", "власть", "госдума", "геополитика"),
    "science": ("наука", "science", "исследования", "космос", "физика", "биология"),
    "parenting": ("дети", "родители", "parenting", "мама", "беременность", "воспитание"),
    "jobs": ("вакансии", "работа", "jobs", "удаленка", "карьера", "резюме"),
}


def keywords_for(code: str) -> list[str]:
    """The bundle to search on; ``[]`` for ``any`` or a code this build does not know."""
    return list(BUNDLES.get(code, ()))


def matches(title: str, about: str | None, code: str) -> bool:
    """Does title + about carry any bundle word? ``any`` matches everything."""
    if code == "any":
        return True
    haystack = f"{title} {about or ''}".casefold()
    return any(word in haystack for word in BUNDLES.get(code, ()))
