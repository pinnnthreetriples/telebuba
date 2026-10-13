"""Reference data for account characters: cities by country, language names, laughs.

Plain tables, no behaviour. Cities are large, ordinary ones so a profile reads like
the people who actually hold numbers there; a country missing here gets no city.
"""

from __future__ import annotations

from typing import Final

CITIES_BY_COUNTRY: Final[dict[str, tuple[str, ...]]] = {
    "MX": ("Ciudad de México", "Guadalajara", "Monterrey", "Puebla", "Querétaro", "Mérida"),
    "CO": ("Bogotá", "Medellín", "Cali", "Barranquilla", "Bucaramanga", "Cartagena"),
    "CA": ("Toronto", "Montreal", "Vancouver", "Calgary", "Ottawa", "Edmonton"),
    "US": ("Houston", "Chicago", "Phoenix", "Dallas", "Denver", "Atlanta"),
    "AR": ("Buenos Aires", "Córdoba", "Rosario", "Mendoza", "La Plata"),
    "PE": ("Lima", "Arequipa", "Trujillo", "Cusco", "Piura"),
    "CL": ("Santiago", "Valparaíso", "Concepción", "La Serena", "Antofagasta"),
    "ES": ("Madrid", "Barcelona", "Valencia", "Sevilla", "Zaragoza", "Málaga"),
    "BR": ("São Paulo", "Rio de Janeiro", "Belo Horizonte", "Curitiba", "Recife"),
    "RU": ("Москва", "Санкт-Петербург", "Казань", "Екатеринбург", "Новосибирск", "Самара"),
    "KZ": ("Алматы", "Астана", "Шымкент", "Караганда"),
    "BY": ("Минск", "Гомель", "Брест", "Гродно"),
    "UA": ("Київ", "Львів", "Одеса", "Дніпро", "Харків"),
    "GB": ("London", "Manchester", "Birmingham", "Leeds", "Bristol"),
    "DE": ("Berlin", "Hamburg", "München", "Köln", "Leipzig"),
    "FR": ("Paris", "Lyon", "Marseille", "Toulouse", "Nantes"),
}

# The model is told which language to write in by name; an unlisted code is passed as is.
LANGUAGE_NAMES: Final[dict[str, str]] = {
    "es": "Spanish",
    "en": "English",
    "ru": "Russian",
    "pt": "Portuguese",
    "fr": "French",
    "de": "German",
    "uk": "Ukrainian",
    "kk": "Kazakh",
    "be": "Belarusian",
    "it": "Italian",
    "tr": "Turkish",
}

# How people laugh in a chat, per language; the fallback is the international one.
LAUGHS: Final[dict[str, tuple[str, ...]]] = {
    "es": ("jajaja", "jaja", "jajajaja"),
    "pt": ("kkkk", "kkkkk", "haha"),
    "ru": ("ахах", "ахаха", "хах"),  # noqa: RUF001 - Cyrillic on purpose
    "uk": ("ахах", "хаха"),  # noqa: RUF001 - Cyrillic on purpose
    "fr": ("mdr", "ahah"),
    "en": ("haha", "lol", "hahaha"),
}
DEFAULT_LAUGHS: Final[tuple[str, ...]] = ("haha", "hahaha")

# Everyday emoji a chat line may end with when the account uses them.
CHAT_EMOJI: Final[tuple[str, ...]] = ("🙂", "😅", "😂", "🙌", "👍", "😊", "🤔", "🔥")

# The word that goes with a post forwarded to a friend, per language.
FORWARD_WORDS: Final[dict[str, str]] = {
    "es": "mira",
    "pt": "olha",
    "en": "look",
    "fr": "regarde",
    "ru": "смотри",
    "uk": "дивись",
    "de": "schau mal",
}

# Without a usable phone the account keeps the language warming always wrote in.
FALLBACK_LANGUAGE: Final = "ru"
