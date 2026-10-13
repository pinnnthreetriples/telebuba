"""The prompts a warming account's lines come from, in its own language (item 4.2).

The fleet's numbers are Mexican, Canadian and Colombian, so a Russian prompt made
every account write Russian. The instruction is now English (every text model reads
it) and names the language to write in: the account's phone language. With a
character the model also knows who is writing — age, city, job, interests — and an
opener may start from one of those interests instead of a generic hello.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account
from services._account_language import account_language
from services.warming._character import account_character, language_name
from services.warming._character_data import FALLBACK_LANGUAGE
from services.warming._chat_style import chat_style

if TYPE_CHECKING:
    from random import Random

    from schemas._warming_character import AccountCharacter, ChatStyle
    from schemas.warming import WarmingSettingsSecret

_PERSONA = (
    "You are {age}, {gender}, living in {place}. You work as {occupation} and you are "
    "into {interests}. Never say you are an AI."
)
_RULES = (
    "Write in {language}, casually, like a real person texting a friend: one or two "
    "short sentences, no quotes, no hashtags, no emoji spam. Only the message text."
)
_OPENERS = (
    "Write one short message to start a chat with a friend in Telegram.",
    "Write a short, lively line you would send a friend in Telegram out of the blue.",
    "Write a short message to a friend in Telegram, as if you just thought of them.",
)
_TOPIC_OPENER = "Write one short message to a friend in Telegram about {interest}."
_REPLY = "Reply briefly and warmly to this Telegram message from your friend: «{incoming}»."
_HISTORY = (
    "You and your friend are chatting in Telegram. The latest messages "
    "(«Me» is you):\n{transcript}\n\n{instruction}"
)
_REPLY_INSTRUCTION = "Continue naturally: answer your friend's last message."
_OPENER_INSTRUCTION = (
    "Write a short message that naturally picks the conversation up again, given what "
    "you talked about before."
)
# Share of cold openers that start from one of the character's interests.
_TOPIC_OPENER_SHARE = 0.5
# Transcript labels, in the instruction's language.
ME, FRIEND = "Me", "Friend"


@dataclass(frozen=True)
class Voice:
    """Who writes and how: the language, the character (if made) and the manner."""

    language: str
    character: AccountCharacter | None = None
    style: ChatStyle | None = None


async def account_voice(account_id: str, secret: WarmingSettingsSecret) -> Voice:
    """The account's language from its phone, its character and its manner."""
    character = await account_character(account_id, secret)
    if character is not None:
        language = character.language
    else:
        account = await fetch_account(account_id)
        language = account_language(account.phone if account else None) or FALLBACK_LANGUAGE
    style = chat_style(account_id) if settings.warming.chat_style_enabled else None
    return Voice(language=language, character=character, style=style)


def _persona_line(character: AccountCharacter | None) -> str | None:
    if character is None:
        return None
    place = ", ".join(part for part in (character.city, character.country) if part)
    return _PERSONA.format(
        age=character.age,
        gender="a woman" if character.gender == "female" else "a man",
        place=place or "your country",
        occupation=character.occupation,
        interests=", ".join(character.interests),
    )


def _compose(voice: Voice, task: str) -> str:
    rules = _RULES.format(language=language_name(voice.language))
    return "\n".join(part for part in (_persona_line(voice.character), task, rules) if part)


def reply_prompt(voice: Voice, incoming: str, transcript: str) -> str:
    """Answer ``incoming``; with history, continue the conversation instead."""
    if transcript:
        task = _HISTORY.format(transcript=transcript, instruction=_REPLY_INSTRUCTION)
    else:
        task = _REPLY.format(incoming=incoming)
    return _compose(voice, task)


def opener_prompt(voice: Voice, transcript: str, rng: Random) -> str:
    """Pick a conversation up again, or open one — from an interest when there is one."""
    if transcript:
        task = _HISTORY.format(transcript=transcript, instruction=_OPENER_INSTRUCTION)
    elif voice.character is not None and rng.random() < _TOPIC_OPENER_SHARE:
        task = _TOPIC_OPENER.format(interest=rng.choice(voice.character.interests))
    else:
        task = rng.choice(_OPENERS)
    return _compose(voice, task)
