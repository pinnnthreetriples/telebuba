"""Each account's own way of writing, applied by code to the model's line (item 4.2).

The model writes what to say; how it looks is the account's: short or medium lines,
emoji or none, an occasional typo, a laugh in its language, no final period, a small first
letter. The manner is drawn from the account id with the fleet's salted hash, so it
never changes between lines and needs no storage. Applied after
``_chat_text._sanitize_chat_text``, before the filter and the dedup see the line.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from schemas._warming_character import ChatStyle, EmojiHabit, MessageLength
from services.warming._character_data import CHAT_EMOJI, DEFAULT_LAUGHS, LAUGHS
from services.warming._fleet import _stable_fraction

if TYPE_CHECKING:
    from random import Random

# Pictographs, dingbats and the emoji variation selector, by code point.
_EMOJI_RE = re.compile(f"[{chr(0x1F300)}-{chr(0x1FAFF)}{chr(0x2600)}-{chr(0x27BF)}{chr(0xFE0F)}]")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?…])\s+")
_WORD_RE = re.compile(r"[^\W\d_]{4,}")
# Per-line chances of the habits that are not on every line.
_LAUGH_CHANCE = 0.25
_EMOJI_CHANCE = 0.5
_MIN_SHORT_CHARS = 8
# Fleet shares of each habit: (upper bound of the hash fraction, habit), first match wins.
_SHORT_SHARE = 0.45
_EMOJI_HABITS: tuple[tuple[float, EmojiHabit], ...] = (
    (0.35, "none"),
    (0.8, "rare"),
    (1.1, "often"),
)
_TYPO_RATES: tuple[tuple[float, float], ...] = ((0.5, 0.0), (0.85, 0.05), (1.1, 0.12))
_LAUGHER_SHARE = 0.5
_NO_PERIOD_SHARE = 0.75
_LOWERCASE_SHARE = 0.4


def _fraction(account_id: str, trait: str) -> float:
    return _stable_fraction(f"style:{trait}:{account_id}")


def chat_style(account_id: str) -> ChatStyle:
    """The account's manner: the same every time, different across the fleet."""
    length: MessageLength = "medium"
    if _fraction(account_id, "length") < _SHORT_SHARE:
        length = "short"
    emoji = next(h for cut, h in _EMOJI_HABITS if _fraction(account_id, "emoji") < cut)
    typo_rate = next(r for cut, r in _TYPO_RATES if _fraction(account_id, "typo") < cut)
    return ChatStyle(
        length=length,
        emoji=emoji,
        typo_rate=typo_rate,
        laughs=_fraction(account_id, "laugh") < _LAUGHER_SHARE,
        no_final_period=_fraction(account_id, "period") < _NO_PERIOD_SHARE,
        lowercase=_fraction(account_id, "lower") < _LOWERCASE_SHARE,
    )


def _first_sentence(text: str) -> str:
    first = _SENTENCE_END_RE.split(text, maxsplit=1)[0].strip()
    return first if len(first) >= _MIN_SHORT_CHARS else text


def _with_emoji(text: str, habit: EmojiHabit, rng: Random) -> str:
    if habit == "none":
        return re.sub(r"\s{2,}", " ", _EMOJI_RE.sub("", text)).strip()
    if habit == "often" and not _EMOJI_RE.search(text) and rng.random() < _EMOJI_CHANCE:
        return f"{text} {rng.choice(CHAT_EMOJI)}"
    return text


def _with_typo(text: str, rng: Random) -> str:
    """Swap two neighbouring letters inside one longer word."""
    words = list(_WORD_RE.finditer(text))
    if not words:
        return text
    word = rng.choice(words)
    i = word.start() + rng.randint(1, len(word.group()) - 3)
    return text[:i] + text[i + 1] + text[i] + text[i + 2 :]


def _drop_final_period(text: str) -> str:
    if text.endswith(".") and not text.endswith(".."):
        return text[:-1].rstrip()
    return text


def apply_style(text: str, style: ChatStyle, language: str, rng: Random) -> str:
    """The line as this account would type it; never empty if ``text`` was not."""
    styled = _first_sentence(text) if style.length == "short" else text
    styled = _with_emoji(styled, style.emoji, rng) or styled
    if style.laughs and rng.random() < _LAUGH_CHANCE:
        styled = f"{rng.choice(LAUGHS.get(language, DEFAULT_LAUGHS))} {styled}"
    if style.typo_rate and rng.random() < style.typo_rate:
        styled = _with_typo(styled, rng)
    if style.no_final_period:
        styled = _drop_final_period(styled)
    if style.lowercase and styled[:1].isupper():
        styled = styled[0].lower() + styled[1:]
    return styled or text
