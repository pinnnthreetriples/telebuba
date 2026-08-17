"""The gate an autoreply has to pass. Pure functions, so a string is the whole test.

Every case here is written from the attacker's side rather than the model's: the
question is not "does a well-behaved answer pass" but "which of the things an
injected answer would have to contain can reach a chat".
"""

from __future__ import annotations

import pytest

from core.config import settings
from services.neuroshilling._reply_guard import clean_reply

_PROVOKING = "кто-нибудь пробовал доставку тут?"


@pytest.mark.parametrize(
    "candidate",
    [
        # Explicit scheme, the obvious one.
        "смотри тут https://evil.example/win",
        # No scheme at all, and still a live link in every Telegram client — which
        # is what makes "block http://" on its own worthless.
        "смотри тут evil.top",
        "www.evil.example рекомендую",
        "пиши в t.me/evil_bot",
        "tg://resolve?domain=evil",
        # A mention is a link too, and it needs no domain.
        "спроси у @evil_support_bot",
        # A number to call and an address to pay: the two payloads worth writing.
        "звони +7 916 123 45 67",
        "0x1234567890abcdef1234567890abcdef12345678",
        "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
        "перейди по joinchat и получи бонус",
        "ключ +AbCdEfGhIjKlMnOpQr",
        # An opaque blob is how an arbitrary payload travels without punctuation.
        "aGVsbG8gd29ybGQgdGhpcyBpcyBiYXNlNjQgcGF5bG9hZA",
        # base64URL, whose whole difference from base64 is the two characters an
        # alphanumeric class leaves out.
        "abcdefghij-klmnopqrst-uvwxyz0123-QQ",
        # A suffix ALLOW-LIST is a list of the TLDs somebody thought of. Every one of
        # these is a live link in a Telegram client and none was on ours.
        "bit.ly/3xK9pQ",
        "goo.gl/maps/xyz",
        "mycrypto.tv",
        "shop-billig.de",
        "promo.tk",
        "bonus.pw",
        # One-character labels, which a two-character minimum on the label missed
        # even for suffixes that WERE listed.
        "t.co/AbCdEf",
        "x.com/durov",
        # An IDN is pure script on both sides of the dot, so per-word mixed-script
        # cannot see it either — the split is on the dot and both halves are Cyrillic.
        "скидки-озон.рф",
        "т.ме/durov",
        # And the IDN registries a Latin-plus-Cyrillic-plus-Arabic suffix class left
        # out. Every one of these is delegated and every one of them previews.
        "жми evil.中国",
        "тут evil.みんな",
        "смотри evil.संगठन",
        "здесь evil.קום",
        "лови evil.ελ",
        "маг.中国",
        # The same shapes spelled in compatibility forms. Nothing here folded the
        # OUTPUT side, so a fullwidth domain was not a domain and a fullwidth ``@``
        # was not a mention — to us. Telegram resolves all of them.
        "ｅｖｉｌ．ｃｏｍ",
        "evil．com",
        "evil。com",
        "evil․com",
        "＠evil_support_bot",
        "ｔ．ｍｅ／ｅｖｉｌ",
        "звони ８９１６１２３４５６７",
        # A number spelled with the letters that look like digits, and one spaced with
        # five characters between each pair — both dialable, and neither one a run of
        # adjacent digits.
        "8 9OO l23 45 67",
        "8 - - 9 - - 1 - - 6 - - 1 - - 2 - - 3 - - 4 - - 5",
        # The same phone number, spelled with separators that are not ASCII. The
        # hyphen here is an en dash, and the middot and the slash are not punctuation
        # any digit-and-separator class thought to include.
        "звони +7–905–123–45–67",
        "тел 7·905·123·45·67",
        "тел 7/905/123/45/67",
        "карта 2202–2061–1234–5678",
    ],
)
def test_a_link_or_a_payload_in_any_spelling_is_refused(candidate: str) -> None:
    """Telegram fetches a link preview server-side, so a URL needs nobody to click it.

    That is why this is a parser and not a line in the prompt: the model was told
    not to write these, and a model told not to do something is not a control.
    """
    verdict = clean_reply(candidate, [_PROVOKING])

    assert verdict.text is None
    assert verdict.reason == "banned_pattern"


def test_a_markdown_link_cannot_launder_its_target_past_the_scan() -> None:
    """The strip deletes the target, so the CLEANED text is innocent — the raw is not.

    Scanning only what would be sent would pass this, and scanning only the raw
    would miss a payload the strip introduces. Both are scanned.
    """
    verdict = clean_reply("[жми сюда](https://evil.example)", [_PROVOKING])

    assert verdict.text is None
    assert verdict.reason == "banned_pattern"


def test_a_price_is_not_a_phone_number() -> None:
    """The rule counts DIGITS, and a price has fewer of them than a number to call."""
    verdict = clean_reply("вышло тысяч 1 000 000 за год, дороговато", [_PROVOKING])

    assert verdict.reason is None


@pytest.mark.parametrize(
    "candidate",
    [
        "это стоит 1 000 000 рублей",
        # Abbreviations that end in a dot and are not domains. The two-character
        # minimum on the suffix is what tells them apart, with no allow-list.
        "фрукты, овощи и т.д. всё свежее",
        "т.е. доставка бесплатная",
        "взял новый iPhone, доволен",
        "ну... такое себе, если честно",
        "не знаю, у меня всё нормально работает уже полгода",
        # The digit fold is why these are here. ``б`` and ``з`` are ordinary Russian
        # letters as well as digit lookalikes, so folding them everywhere turns a
        # sentence with a price in it into a phone number; folded only inside a token
        # that already holds a digit, they stay letters.
        "стоит 1 000 000 руб за штуку, брал в прошлом году",
        "бабушка забыла заброшенную базу, объезд был близко",
        "брал за 300, было бы дешевле в заказе за 250 руб",
        "в 2020 и в 2024 брал тоже",
    ],
)
def test_ordinary_prose_is_still_published(candidate: str) -> None:
    """A gate that refuses everything is not a gate.

    Every rule above errs toward refusal on purpose, which is only defensible while
    the answers a real chat would produce still go out.
    """
    assert clean_reply(candidate, [_PROVOKING]).text is not None


@pytest.mark.parametrize(
    "extra",
    [
        # A format character, which the ``C*`` categories cover.
        "\u200b",
        # Blanks that are LETTERS and SYMBOLS rather than format characters, so a
        # rule written on those categories alone waves them through: the Hangul
        # filler and the braille blank both render as nothing at all.
        "ㅤ",
        "⠀",
        # And the two ``Mn`` blocks beside the variation selectors a first pass
        # remembered: the supplement, and Mongolian's free variation selectors.
        "\U000e0100",
        "᠋",
    ],
)
def test_an_invisible_character_is_refused(extra: str) -> None:
    """Characters that render as nothing hide a payload inside what reads as prose."""
    verdict = clean_reply(f"норм{extra}ально, беру", [_PROVOKING])

    assert verdict.text is None
    assert verdict.reason == "bad_charset"


def test_a_word_mixing_alphabets_is_refused() -> None:
    """The homoglyph tell: ``раypal`` is not ``paypal`` and no reader can see that.

    Per WORD, so a Russian sentence containing ``iPhone`` is left alone. The scripts
    are read off the letters rather than named in a pair, because naming Latin and
    Cyrillic leaves every other alphabet's lookalikes — here Greek upsilon — clean.
    """
    assert clean_reply("зашёл на раypal вчера", [_PROVOKING]).reason == "mixed_script"
    assert clean_reply("зашёл на paυpal вчера", [_PROVOKING]).reason == "mixed_script"
    assert clean_reply("взял iPhone, доволен", [_PROVOKING]).reason is None


def test_an_answer_that_echoes_the_message_is_dropped() -> None:
    """Reproducing attacker text verbatim is the cheapest injection there is.

    It is also how a payload re-enters our own context: the reply lands in the same
    chat the next poll reads, this time flagged as ours.
    """
    verdict = clean_reply(_PROVOKING, [_PROVOKING])

    assert verdict.text is None
    assert verdict.reason == "echo"


def test_a_short_lift_out_of_a_long_message_is_dropped_too() -> None:
    """Jaccard divides by the UNION, so a long message dilutes its own payload.

    Eight tokens taken verbatim out of forty score 0.2 against a 0.6 threshold and
    would have been published. Containment divides by the ANSWER instead.
    """
    long_message = " ".join(f"слово{index}" for index in range(40))
    lift = " ".join(f"слово{index}" for index in range(8))

    assert clean_reply(lift, [long_message]).reason == "echo"


def test_a_short_answer_made_of_the_message_s_own_words_is_not_a_lift() -> None:
    """Containment's own blind spot, closed by a floor under its denominator.

    Two words that both appear in the message are 100% contained in it and are also
    what half the real answers in a chat look like.
    """
    assert clean_reply("пробовал доставку", [_PROVOKING]).text is not None


@pytest.mark.parametrize(("candidate", "reason"), [("", "empty"), ("   \n  ", "empty")])
def test_an_empty_answer_is_refused(candidate: str, reason: str) -> None:
    assert clean_reply(candidate, [_PROVOKING]).reason == reason


def test_a_long_answer_is_refused_rather_than_truncated() -> None:
    """A cut sentence reads as a broken bot, and a payload can survive the cut."""
    verdict = clean_reply("а" * (settings.neuroshilling.reply_max_chars + 1), [_PROVOKING])

    assert verdict.text is None
    assert verdict.reason == "too_long"


def test_a_wordy_answer_is_refused_even_when_it_is_short_enough() -> None:
    words = " ".join(["да"] * (settings.neuroshilling.reply_max_words + 1))

    assert clean_reply(words, [_PROVOKING]).reason == "too_long"


def test_a_clean_answer_comes_back_flattened_to_one_line() -> None:
    """A multi-line answer can forge a quote or a system notice; one line cannot."""
    verdict = clean_reply("  да,\n  вполне  норм **сойдёт**  ", [_PROVOKING])

    assert verdict == ("да, вполне норм сойдёт", None)
