"""The shared language detector: discovery's script vote and warming's Latin split."""

from __future__ import annotations

import pytest

from services._lang_detect import detect_language, detect_script_language


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Las mejores noticias del día, con todo lo que pasa en la ciudad y más", "es"),
        ("Os melhores vídeos do dia, com tudo o que não pode perder e muito mais", "pt"),
        ("The best news of the day, with everything that is going on in the city", "en"),
        ("Les meilleures nouvelles du jour pour vous, avec tout ce qui se passe", "fr"),
        ("Die besten Nachrichten des Tages und alles, was in der Stadt passiert ist", "de"),
        ("Новости дня: всё, что происходит в городе, коротко и по делу", "ru"),
        ("Новини дня: усе, що відбувається у місті", "uk"),
        ("今日のニュース", "other"),
        ("", None),
    ],
)
def test_detect_language_names_each_supported_language(text: str, expected: str | None) -> None:
    assert detect_language(text) == expected


def test_a_short_latin_title_stays_english_when_the_words_say_too_little() -> None:
    # One stop word is not a vote: a brand name with a single "el" is not Spanish.
    assert detect_language("El Crypto Daily") == "en"


def test_the_script_vote_never_splits_latin() -> None:
    # Discovery's filter keeps the coarse verdict, so its behaviour does not move.
    assert detect_script_language("Las mejores noticias del día con todo lo que pasa") == "en"
