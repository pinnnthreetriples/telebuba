"""The words @SpamBot's replies are read by, one :class:`Lexicon` per language.

Pure data. ``services._spam_parse`` matches a reply against the union of every lexicon (the bot
localises its reply to the account's interface language, so the language is not known in
advance) and :func:`language_of` says which one a text belongs to.

**Lexicons are not invented.** ``en`` and ``ru`` are exactly the markers the pre-gateway parser
shipped with. There is no German, Spanish, Portuguese, French, ... entry because no captured
sample or owner-confirmed text of those replies exists in the repository: until one does, a reply
in such a language falls through to the structural read (a date) and otherwise stays unreadable
(``unrecognized_reply``), which the strict parser answers with ``unknown``
(docs/spam-intel.md 5.8, risks R3).

Adding a language is one data entry and one fixture:

1. Append a :class:`Lexicon` to ``LEXICONS`` with its stems, lowercase, taken from a REAL reply
   (the redacted samples in ``spam_reply_samples``, the history sweep, or a text the owner
   confirms). A stem must not occur in a reply of the opposite verdict: the Russian "ограничен"
   appears in both the clean and the limited reply, which is why only "наложены" marks a limit.
2. Add that language's replies to ``tests/services/spam_intel/_reply_fixtures.py`` (a clean, a
   limited and a being-checked text). ``test_parse_lexicons.py`` walks ``LEXICONS`` and fails a
   language that has no fixtures, or whose fixtures the parser does not read as labelled.

Nothing else changes: the flat marker tuples below are derived.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Lexicon:
    """The substring markers of one language, lowercase.

    ``clean``: no limits. ``being_checked``: an automated review is in progress. ``limited``: an
    account restriction. ``automated``: a stem that, when nothing above matched, still says a
    review is in progress.
    """

    lang: str
    clean: tuple[str, ...]
    being_checked: tuple[str, ...]
    limited: tuple[str, ...]
    automated: tuple[str, ...] = ()


# ``@SpamBot`` localises its reply to the account's interface language, so the same verdict can
# land in EN, RU, or anything else; matching by short stems lets one rule cover several
# inflections of one language.
_EN = Lexicon(
    lang="en",
    # "Good news, no limits are currently applied to your account."
    clean=("no limits", "good news"),
    # "Your account is being checked."
    being_checked=("being checked", "is checked"),
    # "Your account is now limited until ..."
    limited=("limited", "restricted"),
    # The Latin stem also covers "automatisch", "automatique", "automatizzato", "automatizado".
    automated=("automat",),
)
_RU = Lexicon(
    lang="ru",
    # Long form: "Хорошие новости, ... не применены никакие ограничения." Short form:
    # "Ваш аккаунт свободен от каких-либо ограничений." It has to be matched explicitly because
    # the substring "ограничен" appears in both the clean ("ограничений") and the limited
    # ("наложены ограничения") forms and is therefore not a safe limited marker.
    clean=("хорошие новости", "не применены", "свободен от"),
    # "Ваш аккаунт сейчас проверяется автоматизированной системой."
    being_checked=("сейчас проверя", "автоматизирован"),
    # The Russian limited reply consistently contains the "наложены" stem; it is specific to
    # limited replies and is the only safe Russian discriminator.
    limited=("наложены",),
    automated=("автомат",),
)

LEXICONS: tuple[Lexicon, ...] = (_EN, _RU)

CLEAN_MARKERS = tuple(marker for lexicon in LEXICONS for marker in lexicon.clean)
BEING_CHECKED_MARKERS = tuple(marker for lexicon in LEXICONS for marker in lexicon.being_checked)
LIMITED_MARKERS = tuple(marker for lexicon in LEXICONS for marker in lexicon.limited)
AUTOMATED_MARKERS = tuple(marker for lexicon in LEXICONS for marker in lexicon.automated)


def language_of(text: str) -> str | None:
    """The language tag of the first lexicon with a marker in ``text``, or ``None``."""
    lowered = text.lower()
    for lexicon in LEXICONS:
        markers = (*lexicon.clean, *lexicon.being_checked, *lexicon.limited)
        if any(marker in lowered for marker in markers):
            return lexicon.lang
    return None
