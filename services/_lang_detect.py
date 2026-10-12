"""Text language detection shared by channel discovery and warming — pure, no I/O.

Two grains. :func:`detect_script_language` is discovery's original vote by letter
script (``ru`` / ``uk`` / ``en`` / ``other``), kept byte-for-byte so its filter does
not move. :func:`detect_language` refines the Latin bucket into ``es`` / ``pt`` /
``fr`` / ``de`` / ``en`` by counting each language's commonest short words, which is
enough to tell a Mexican channel from an American one without a model or a dependency.
"""

from __future__ import annotations

import re

_UKRAINIAN_MARKERS = frozenset("іїєґІЇЄҐ")

# Function words, each frequent in its own language and rare in the others. Words two
# of these languages share (``a``, ``de``, ``la``, ``que``, ``das``, ``es``) are left out on
# purpose: they would vote twice and blur exactly the pairs this exists to split.
_STOP_TEXT: dict[str, str] = {
    "en": "the and of to is that it for you with this are was be have not from at by your"
    " we they will can",
    "es": "el los las del por con una lo más pero sus al muy también hay ya sin sobre y fue",
    "pt": "os dos não uma com é mas seu sua também muito já são isso pelo pela ao",
    "fr": "le les du et est une pour dans pas qui sur au avec ce sont plus nous vous il elle cette",
    "de": "der die und ist nicht ein eine zu den mit von sich auf für dem auch im ich sie wir wird",
}
_STOP_WORDS = {lang: frozenset(words.split()) for lang, words in _STOP_TEXT.items()}
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
# Below this many stop-word hits the text says too little to pick a Latin language.
# Warming stores the verdict as a label no later read overwrites, so a guess would
# stick: too little evidence answers ``None`` and leaves the channel unlabelled.
_MIN_STOP_HITS = 2


def _is_cyrillic(char: str) -> bool:
    return "Ѐ" <= char <= "ӿ"


def _is_latin(char: str) -> bool:
    return char.isascii() or "À" <= char <= "ɏ"


def detect_script_language(text: str) -> str | None:
    """``ru`` / ``en`` / ``uk`` / ``other`` by letter script; ``None`` when no letters.

    Majority vote over letters only, so hashtags, digits and emoji do not sway it.
    Ukrainian is Cyrillic with any of the four letters Russian lacks (``іїєґ``).
    """
    letters = [char for char in text.strip() if char.isalpha()]
    if not letters:
        return None
    half = len(letters) / 2
    if sum(map(_is_cyrillic, letters)) > half:
        return "uk" if _UKRAINIAN_MARKERS.intersection(letters) else "ru"
    if sum(map(_is_latin, letters)) > half:
        return "en"
    return "other"


def _latin_language(text: str) -> str | None:
    words = [word.casefold() for word in _WORD.findall(text)]
    hits = {lang: sum(word in stops for word in words) for lang, stops in _STOP_WORDS.items()}
    best = max(hits, key=lambda lang: hits[lang])
    return best if hits[best] >= _MIN_STOP_HITS else None


def detect_language(text: str) -> str | None:
    """The script verdict, with a Latin text narrowed to ``es``/``pt``/``fr``/``de``/``en``.

    ``None`` for a Latin text too short to tell which (the script vote would call it
    ``en``), as well as for a text with no letters.
    """
    script = detect_script_language(text)
    return _latin_language(text) if script == "en" else script
