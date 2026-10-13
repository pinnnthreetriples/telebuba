"""Chat manner and language (item 4.2): code-applied style, prompts in the phone's language."""

from __future__ import annotations

import random

import pytest

from core.config import settings
from core.db import create_account, load_warming_settings
from schemas._warming_character import AccountCharacter, ChatStyle
from schemas.accounts import AccountCreate
from schemas.gemini import GeminiResult
from services.warming import _seams
from services.warming._chat_prompt import Voice, account_voice, opener_prompt, reply_prompt
from services.warming._chat_style import apply_style, chat_style
from services.warming._chat_text import _generate_chat_text

_CHARACTER = AccountCharacter(
    account_id="acc-1",
    language="es",
    country="MX",
    age=27,
    gender="female",
    city="Guadalajara",
    occupation="Diseñadora",
    interests=["fútbol", "cine", "cocina"],
)


class _Always(random.Random):
    """Every chance passes; choices still come from the seeded stream."""

    def random(self) -> float:
        return 0.0


def _rng() -> random.Random:
    return random.Random(5)  # noqa: S311 - deterministic test rng


def test_the_manner_is_stable_per_account_and_varies_across_the_fleet() -> None:
    assert chat_style("acc-1") == chat_style("acc-1")
    styles = [chat_style(f"acc-{i}") for i in range(60)]
    assert len({s.length for s in styles}) == 2
    assert len({s.emoji for s in styles}) == 3
    assert {s.laughs for s in styles} == {True, False}
    assert {s.lowercase for s in styles} == {True, False}


def test_a_short_talker_keeps_one_sentence_without_the_final_period() -> None:
    style = ChatStyle(length="short", emoji="rare", no_final_period=True)
    text = "Hola, ¿cómo estás hoy? Yo bien, trabajando mucho."
    assert apply_style(text, style, "es", _rng()) == "Hola, ¿cómo estás hoy?"
    assert apply_style("Todo bien.", style, "es", _rng()) == "Todo bien"


def test_no_emoji_strips_them_and_often_adds_one() -> None:
    plain = apply_style("qué tal 😂", ChatStyle(emoji="none"), "es", _rng())
    assert plain == "qué tal"

    added = apply_style("qué tal", ChatStyle(emoji="often"), "es", _Always(1))
    assert added != "qué tal"
    assert added.startswith("qué tal ")


def test_the_laugh_is_in_the_accounts_language() -> None:
    style = ChatStyle(laughs=True, no_final_period=False)
    assert apply_style("qué risa", style, "es", _Always(1)).split()[0].startswith("ja")
    assert apply_style("смешно", style, "ru", _Always(1)).split()[0] in {"ахах", "ахаха", "хах"}


def test_a_typo_swaps_two_inner_letters_and_keeps_the_letters() -> None:
    text = "mañana trabajo temprano"
    typo = apply_style(text, ChatStyle(typo_rate=0.1, no_final_period=False), "es", _Always(2))
    assert typo != text
    assert sorted(typo) == sorted(text)
    assert typo[0] == text[0]


def test_lowercase_lowers_only_the_first_letter() -> None:
    style = ChatStyle(lowercase=True, no_final_period=False)
    assert apply_style("Hola Juan", style, "es", _rng()) == "hola Juan"


def test_prompts_name_the_language_and_the_character() -> None:
    voice = Voice(language="es", character=_CHARACTER)
    prompt = reply_prompt(voice, "hola!", "")
    assert "Write in Spanish" in prompt
    assert "Diseñadora" in prompt
    assert "Guadalajara" in prompt
    assert "«hola!»" in prompt
    bare = opener_prompt(Voice(language="en"), "", _rng())
    assert "Write in English" in bare
    assert "You are" not in bare


@pytest.mark.asyncio
async def test_the_voice_follows_the_phone_without_a_character() -> None:
    await create_account(AccountCreate(account_id="acc-mx", phone="+525512345678"))
    voice = await account_voice("acc-mx", await load_warming_settings())
    assert voice.language == "es"
    assert voice.character is None


@pytest.mark.asyncio
async def test_the_style_is_applied_before_the_dedup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "chat_style_enabled", True)

    async def gen(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="Todo bien. Y tú qué tal?")

    monkeypatch.setattr(_seams, "generate_text", gen)
    voice = Voice(language="es", style=ChatStyle(length="short", no_final_period=True))
    result = await _generate_chat_text("acc-1", await load_warming_settings(), "p", voice, [])
    assert result.text == "Todo bien"
