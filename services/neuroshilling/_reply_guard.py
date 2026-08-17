"""The gate an autoreply has to pass before an account is allowed to publish it.

**This module is the boundary.** The fence in ``_prompt`` is depth behind it:
delimiter defences are documented to fall to adaptive attacks, so nothing here
assumes the model obeyed anything it was told. Every check below runs on the
STRING THAT WOULD BE SENT, in code, with no request to the model involved.

Why the shapes it refuses are the shapes it refuses:

* **Links, in every spelling.** Telegram fetches a link preview SERVER-SIDE the
  moment a message is posted, so a URL our account emits is an exfiltration and
  attribution channel that needs nobody to click it. The scheme forms, the bare
  ``t.me`` and ``www.`` forms and ANYTHING DOMAIN-SHAPED are all refused, and the
  scan runs on the raw model output as well as on the cleaned text — otherwise
  ``[click](http://evil)``, whose target the markdown strip removes, would sail
  through as a clean word. The domain rule denies a shape rather than allowing a
  list of suffixes, because a suffix list is a list of the TLDs somebody thought
  of: ``bit.ly``, ``goo.gl``, ``promo.tk`` and ``скидки-озон.рф`` are all outside
  one and all publish a live link.
* **``@mentions``.** A mention is a live link to a channel or an account, and it
  is how a hijacked reply advertises somewhere else without a URL.
* **Phone-shaped runs and wallet-shaped strings.** The two payloads an injected
  reply is worth writing: a number to call and an address to pay.
* **Invite keys and long opaque tokens.** ``+HASH`` and base64/hex blobs are the
  smuggling formats — an invite that needs no domain, and an arbitrary payload
  that needs no punctuation.
* **Invisible characters and mixed-script words.** Format and control characters
  hide a payload inside what looks like plain prose, and a word whose letters come
  from more than one Unicode SCRIPT is the homoglyph tell — that is how a domain
  that reads as ``paypal`` is not the one anybody typed. The scripts are read off
  each letter rather than compared against a pair of alphabets: naming Latin and
  Cyrillic leaves the same disguise spelled with a Greek upsilon perfectly clean.
* **Echoes.** An answer too close to the message that provoked it is the model
  doing what the message told it to; reproducing attacker text verbatim is the
  cheapest injection there is, and it is also how a payload re-enters our own
  context on the next poll. Measured two ways, because one of them has a blind
  spot: Jaccard divides by the union, so a short lift out of a long message
  scores near zero, and containment divides by the ANSWER instead.

Every rule runs over three spellings of the same answer: what the model returned,
what would be sent, and that with its compatibility forms folded. The third is not
optional — a domain written in fullwidth letters is not a domain, a fullwidth
commercial at is not a mention and a run of fullwidth digits is not a phone number
to any regex written in ASCII, and Telegram resolves all three.

Refusals are reason CODES, never text: the caller logs them, and ``extra`` on a
log event is an HTTP response body, so nothing attacker-controlled may go in it.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final, NamedTuple

from core.config import settings
from services.content import normalize_text, similarity, strip_markdown_delimiters

# Reason codes. ``empty`` and ``too_long`` are the vocabulary's existing spellings
# and are reused deliberately; the rest are this gate's own.
_EMPTY: Final = "empty"
_TOO_LONG: Final = "too_long"
_BANNED_PATTERN: Final = "banned_pattern"
_BAD_CHARSET: Final = "bad_charset"
_MIXED_SCRIPT: Final = "mixed_script"
_ECHO: Final = "echo"

# One alternation rather than a ladder, because it is scanned twice per candidate
# and every branch has the same verdict: refuse. ``_DIGIT_RUN`` is the one rule that
# is NOT here, because it is about a density of digits rather than a shape.
_BANNED = re.compile(
    r"""
      https?://
    | \bwww\.
    | \b(?:t|telegram)\.me\b
    | \btg://
    # Anything domain-SHAPED: "look at evil.top" is a working link in every Telegram
    # client, with no scheme anywhere in the text, and so is every suffix a
    # hard-coded list forgets. The label is ``+`` rather than ``{2,}`` because
    # ``t.co`` and ``x.com`` have one-character labels; the suffix is ANY letter
    # rather than a list of scripts, because an IDN is pure script on BOTH sides of
    # the dot — the split is on the dot, so per-word mixed-script cannot see it — and
    # ".中国", ".みんな", ".संगठन", ".קום" and ".ελ" are live registries that a
    # Latin-plus-Cyrillic-plus-Arabic class published working links for.
    # The two-character minimum on the suffix leaves the one-letter abbreviations without
    # an allow-list of its own: Russian's "и т.д." and English's "e.g." both end in a
    # single letter, and no registry sells a one-character suffix.
    | \b[\w-]+\.[^\W\d_]{2,24}\b
    # A mention, but not an email's local-part-then-domain and not a path segment.
    | (?<![\w@/])@[A-Za-z][A-Za-z0-9_]{3,}
    | \bjoinchat\b
    # A Telegram invite key without its domain; the real ones are 16 characters.
    | \+[A-Za-z0-9_-]{15,}
    | \b0x[0-9a-fA-F]{20,}\b
    | \b(?:bc1|ltc1)[a-z0-9]{20,}\b
    | \b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b
    | \bT[1-9A-HJ-NP-Za-km-z]{33}\b
    # Base64 / hex / any opaque blob. ``-`` and ``.`` are IN the class: base64url
    # and a dotted payload are precisely the spellings a strict alphanumeric class
    # waves through. Cyrillic is not, so this cannot fire on the language most of
    # these replies are written in, and the bound is 22 rather than 30 because a
    # shorter code is still a code. The price is a 22-character hyphenated English
    # compound, which this gate is content to lose.
    | [A-Za-z0-9+/=_.-]{22,}
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Nine digits with at most three characters of anything between them. The shortest
# international number is 9 digits (E.164 minus the country-code floor), so this
# leaves "1 000 000" a price at seven — and counting digits rather than matching a
# separator CLASS is what makes it blind to which separator was used: an en dash, a
# middot and a slash all read as a phone number and none of them is ASCII ``-``.
# The second branch is what the first missed: "8 - - 9 - - 1" spaces its digits five
# characters apart and is still dialable. The branches are cut at four characters so
# they cannot both match one gap.
_DIGIT_RUN = re.compile(r"\d(?:(?:\D{0,3}|\W{4,12})\d){8,}")
# Letters that stand in for a digit. Folded PER TOKEN and only inside a token that
# already holds a real digit, which is the whole difference between a number spelled
# with letters and a word: "9OO" is the first, "рублей" is the second, and a fold
# applied wherever these letters appear refuses the prices this gate must publish.
_DIGIT_LOOKALIKES: Final = str.maketrans(
    {
        "O": "0",
        "o": "0",
        "l": "1",
        "I": "1",
        "|": "1",
        "\u0406": "1",  # Cyrillic capital Byelorussian-Ukrainian I
        "\u0417": "3",  # Cyrillic capital ZE
        "\u0437": "3",  # Cyrillic small ZE
        "\u0431": "6",  # Cyrillic small BE
    },
)
_TOKEN = re.compile(r"\w+")

# Unicode categories with no business in a chat message: control, format
# (zero-width joiners, bidi overrides, the tag block), private use, surrogates.
_INVISIBLE_CATEGORIES: Final = frozenset({"Cc", "Cf", "Co", "Cs"})
# Blanks the categories above do not reach, because they are letters, symbols and
# marks rather than format characters: the Hangul fillers (``Lo``), the braille blank
# (``So``), the Khmer viramas, BOTH variation selector blocks and the Mongolian free
# variation selectors (``Mn``). Every one of them renders as nothing and hides a
# payload inside what reads as a single word.
_INVISIBLE_CHARS: Final = frozenset(
    "\u115f\u1160\u3164\uffa0\u2800\u17b4\u17b5"
    + "".join(map(chr, range(0xFE00, 0xFE10)))
    + "".join(map(chr, range(0xE0100, 0xE01F0)))
    + "".join(map(chr, range(0x180B, 0x180E))),
)

# The dot spellings that separate the labels of a real domain name. UTS #46 maps every
# one of them onto ``.`` before a name is resolved, and NFKC only reaches two.
_DOT_SPELLINGS: Final = str.maketrans(dict.fromkeys("\uff0e\u3002\uff61\u2024\u06d4", "."))
# Combining marks, dropped alongside the compatibility fold. The Indic registries
# interleave letters with spacing marks, so a letter class reads a four-character
# label as one character and the suffix rule's two-character minimum is never met.
_MARKS: Final = frozenset({"Mn", "Mc", "Me"})
# The categories a SCRIPT is read off. Modifier letters (``Lm``) are left out on
# purpose: the apostrophes and prolonged-sound marks in that category are script
# Common, and counting them would make "don't" a two-script word.
_LETTERS: Final = frozenset({"Lu", "Ll", "Lt", "Lo"})

# Jaccard's blind spot: it divides by the UNION, so a four-token verbatim lift out of
# a hundred-token message scores 0.04. Containment divides by the ANSWER instead. The
# floor under that denominator is what keeps "да, согласен" — entirely contained in
# half the messages it could answer — from being read as a lift.
_LIFT_FLOOR: Final = 6
_LIFT_THRESHOLD: Final = 0.8

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


class ReplyVerdict(NamedTuple):
    """What may be published, or why nothing may be.

    Exactly one field is meaningful: ``text`` on a pass, ``reason`` on a refusal.
    """

    text: str | None
    reason: str | None


def _has_invisible(text: str) -> bool:
    return any(
        character in _INVISIBLE_CHARS or unicodedata.category(character) in _INVISIBLE_CATEGORIES
        for character in text
    )


def _folded(text: str) -> str:
    """The same answer with every compatibility spelling of it resolved.

    ``_prompt._scrub`` folds the INPUT side of this feature and nothing folded the
    output side, so a fullwidth domain, a fullwidth ``@`` and an ideographic full stop
    walked past rules written in ASCII while Telegram linkified all three.

    NFKC answers the fullwidth forms and the one-dot leader; the translation answers
    the label separators NFKC deliberately leaves alone; the mark strip answers the
    Indic registries, whose labels a letter class reads as one character.
    """
    normalized = unicodedata.normalize("NFKC", text).translate(_DOT_SPELLINGS)
    return "".join(
        character for character in normalized if unicodedata.category(character) not in _MARKS
    )


def _as_digits(text: str) -> str:
    """The answer with the letters that stand in for digits inside a NUMBER folded.

    Per token, because the fold is only ever right where a real digit already is:
    ``9OO`` is nine hundred spelled with two letters, ``рублей`` is a word, and a
    fold applied to the whole string turns every Russian sentence with a price in it
    into a phone number.
    """
    return _TOKEN.sub(_fold_token, text)


def _fold_token(match: re.Match[str]) -> str:
    token = match.group()
    if not any(character.isdigit() for character in token):
        return token
    return token.translate(_DIGIT_LOOKALIKES)


def _lifted(cleaned: str, provoking: str) -> bool:
    """Is the answer mostly words taken straight out of the message it answers?

    Containment rather than Jaccard, and the two are asked separately because they
    fail on opposite shapes: Jaccard catches an answer that IS the message, this
    catches a short verbatim lift out of a long one, which Jaccard's union
    denominator dilutes to nothing.
    """
    answer = set(normalize_text(cleaned).split())
    source = set(normalize_text(provoking).split())
    return len(answer & source) / max(len(answer), _LIFT_FLOOR) >= _LIFT_THRESHOLD


def _script(character: str) -> str:
    """The script family a letter's Unicode NAME opens with — ``LATIN``, ``GREEK``...

    Read off the name rather than out of a range table, because a range table is a
    list of the alphabets somebody thought of: the last one held Latin and Cyrillic,
    and a Greek upsilon walks a homoglyph domain past both of them.
    """
    return unicodedata.name(character, "").partition(" ")[0]


def _has_mixed_script_word(text: str) -> bool:
    """Does any single word take its letters from more than one script?

    Per WORD and not per message, because a Russian chat writing ``iPhone`` mixes
    alphabets across a sentence all day long and that is ordinary. Inside one word it
    is not: it is either a homoglyph disguise or a typo, and both are worth losing a
    reply over when the alternative is publishing the disguise.
    """
    return any(
        len({_script(character) for character in word if _is_letter(character)}) > 1
        for word in _WORD.findall(text)
    )


def _is_letter(character: str) -> bool:
    return unicodedata.category(character) in _LETTERS


def _shape_reason(candidate: str, cleaned: str) -> str | None:
    """The first structural rule this answer breaks, if it breaks one.

    Every rule runs over all three spellings and not a rule each: ``candidate`` is the
    model's answer with its whitespace collapsed, ``cleaned`` is what would be sent —
    both, because the markdown strip deletes a link's target and would otherwise
    launder it into an innocent-looking word — and the third is ``cleaned`` folded,
    without which the same payloads spelled fullwidth are a different string to every
    regex above.
    """
    texts = (candidate, cleaned, _folded(cleaned))
    if any(_BANNED.search(text) for text in texts):
        return _BANNED_PATTERN
    if any(_DIGIT_RUN.search(_as_digits(text)) for text in texts):
        return _BANNED_PATTERN
    if any(_has_invisible(text) for text in texts):
        return _BAD_CHARSET
    if any(_has_mixed_script_word(text) for text in texts):
        return _MIXED_SCRIPT
    return None


def clean_reply(candidate: str, provoking: str) -> ReplyVerdict:
    """Vet one model answer against the message it answers. ``text=None`` refuses.

    The whitespace collapse comes first and is not cosmetic: a multi-line answer
    can forge the layout of a Telegram conversation — a fake quote, a fake system
    notice — and one line cannot. It also puts the length caps on a single
    normalised string rather than on whatever indentation the model produced.

    ``strip_markdown_delimiters`` follows, because ``parse_mode`` is off on every
    client in this project: markers the model writes would otherwise be published
    literally, which is the machine tell this whole feature exists to avoid.

    The echo test is last of the content rules because it is the only one that
    needs the provoking message, and the cheapest way to fail is not to reach it.
    """
    limits = settings.neuroshilling
    collapsed = " ".join(candidate.split())
    cleaned = " ".join(strip_markdown_delimiters(collapsed).split())
    if not cleaned:
        return ReplyVerdict(None, _EMPTY)
    if len(cleaned) > limits.reply_max_chars or len(cleaned.split()) > limits.reply_max_words:
        # Refused rather than truncated: a cut sentence reads as a broken bot, and
        # a payload that survives the cut is still published.
        return ReplyVerdict(None, _TOO_LONG)
    reason = _shape_reason(collapsed, cleaned)
    if reason is not None:
        return ReplyVerdict(None, reason)
    if similarity(cleaned, provoking) >= limits.reply_echo_threshold or _lifted(cleaned, provoking):
        return ReplyVerdict(None, _ECHO)
    return ReplyVerdict(cleaned, None)
