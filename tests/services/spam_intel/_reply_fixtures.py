"""Reply fixtures per language: the texts a lexicon in ``services._spam_lexicon`` must read.

One entry per lexicon language, from texts the legacy tests already carried (en, ru). A new
language is added HERE only from a captured sample or a text the owner confirms;
``test_parse_lexicons`` fails a lexicon that has no entry. Nothing in this file is invented.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReplyFixtures:
    """One language's replies: a clean one, a limited one with a date, and a being-checked one."""

    clean: str
    limited_dated: str
    being_checked: str


FIXTURES: dict[str, ReplyFixtures] = {
    "en": ReplyFixtures(
        clean="Good news, no limits are currently applied to your account. You're free as a bird!",
        limited_dated="Your account is now limited until 29 Oct 2026, 11:44 UTC.",
        being_checked="Your account is being checked.",
    ),
    "ru": ReplyFixtures(
        clean="Хорошие новости, на ваш аккаунт не применены никакие ограничения.",
        limited_dated="На ваш аккаунт наложены ограничения до 29 окт. 2026.",
        being_checked="Ваш аккаунт сейчас проверяется автоматизированной системой.",
    ),
}

# The second Russian clean form, kept because its stem "ограничен" must NOT read as a limit.
CLEAN_RU_SHORT = "Ваш аккаунт свободен от каких-либо ограничений."

# Languages of the fleet with no lexicon yet: the texts exist only as test inputs of "this must
# stay unreadable", they are not a source of markers.
UNREAD_ES = "Tu cuenta no tiene ninguna restricción en este momento, ¡eres libre!"
UNREAD_ES_LIMITED = "Lamentablemente, tu cuenta está limitada de forma indefinida."
