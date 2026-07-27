"""Tests for ``services.content`` — normalisation, filtering and dedup."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from services import content

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


def test_normalize_collapses_case_and_punctuation() -> None:
    assert content.normalize_text("Hello,  World!!") == "hello world"
    assert content.content_hash("Hello, World!") == content.content_hash("hello world")


def test_similarity_token_set_jaccard() -> None:
    # Same token set once normalised (case/punctuation dropped) → 1.0.
    assert content.similarity("Great post!", "great post") == 1.0
    # Disjoint vocabularies → 0.0.
    assert content.similarity("alpha beta", "gamma delta") == 0.0
    # Partial overlap is intersection / union: {a,b,c} vs {b,c,d} = 2/4.
    assert content.similarity("a b c", "b c d") == pytest.approx(0.5)
    # Two empty strings are identical; one empty is fully dissimilar.
    assert content.similarity("", "") == 1.0
    assert content.similarity("hello", "") == 0.0


def test_strip_markdown_delimiters() -> None:
    """``parse_mode`` is off on every client, so an LLM's markers would SHOW.

    ``**Отличный пост!** Спасибо`` reached a channel comment with the asterisks
    visible — a machine tell on the surfaces built to look human. Only Telethon's
    own delimiter set comes off; a lone ``*`` was never a delimiter.
    """
    assert content.strip_markdown_delimiters("**Отличный пост!** Спасибо") == (
        "Отличный пост! Спасибо"
    )
    assert content.strip_markdown_delimiters("__bold__ `code` ~~gone~~") == "bold code gone"
    assert content.strip_markdown_delimiters("```py\nx\n```") == "py\nx\n"
    assert content.strip_markdown_delimiters("2 * 3 = 6") == "2 * 3 = 6"
    assert content.strip_markdown_delimiters("plain text") == "plain text"
    # A nested pair comes out fully bare, like Telethon's re-scan of the span.
    assert content.strip_markdown_delimiters("**внешний `код` тут**") == "внешний код тут"
    # The one deliberate divergence: Telethon skips past a code span and renders
    # this as ```py```. Stripping the extra pair drops a marker, not a word.
    assert content.strip_markdown_delimiters("``py``") == "py"


def test_strip_markdown_delimiters_takes_the_link_form_too() -> None:
    """``[text](url)`` was the other syntax Telethon's parser consumed.

    ``markdown.DEFAULT_URL_RE`` turned it into the label plus a
    ``MessageEntityTextUrl``, so with parsing off the whole thing posts LITERALLY.
    ``has_link`` does not catch it either — it matches ``https?://`` / ``t.me`` /
    ``telegram.me``, not ``tg://`` — so a generated mention really did go out with
    the brackets visible. Keeping the label and dropping the target is what a reader
    used to see.

    Every assertion here failed pre-fix: the strip only touched delimiters.
    """
    assert content.strip_markdown_delimiters("[тут](tg://user?id=1)") == "тут"
    assert content.strip_markdown_delimiters("см. [канал](https://t.me/x) вот") == ("см. канал вот")
    # Brackets that are not a link stay put: no ``(`` immediately after the ``]``.
    assert content.strip_markdown_delimiters("реакция [смех] тут") == "реакция [смех] тут"
    assert content.strip_markdown_delimiters("см. [1] (сноска)") == "см. [1] (сноска)"


@pytest.mark.parametrize(
    "text",
    [
        "snake_case__name",
        "foo__bar",
        "смотри https://example.com/a__b тут",
        "a lone * star",
        "2 * 3 = 6",
        "a stray ` backtick",
        "unclosed **bold",
        "****",
        # An opening fence an operator wants literal, with nothing closing it.
        "```py\nprint(1)",
    ],
)
def test_strip_markdown_delimiters_leaves_unpaired_markers_alone(text: str) -> None:
    r"""The regression risk lives entirely in the negatives — an UNPAIRED marker.

    Telethon only ever consumed a delimiter with a partner
    (``message.find(delim, i + len(delim) + 1)``; verified against the installed
    1.44.0, where ``foo__bar``, ``snake_case__name``, a URL with one ``__``, a stray
    backtick, ``unclosed **bold`` and ``****`` all parse to themselves with no
    entities). So matching Telethon means LEAVING these, and a blind
    ``re.sub(r"\*\*|__|~~|`", "")`` corrupted every one of them —
    ``snake_case__name`` → ``snake_casename`` on a comment about to be posted.

    That is the deliberate choice on ``foo__bar``: it is preserved, because Telethon
    would NOT have italicised it, so stripping would not have matched what Telegram
    showed — it would have silently rewritten the operator's word.

    Every case here failed pre-fix except the two lone-``*`` ones.
    """
    assert content.strip_markdown_delimiters(text) == text


def test_has_link() -> None:
    assert content.has_link("see https://example.com")
    assert content.has_link("join t.me/foo")
    assert not content.has_link("just a normal sentence")


def test_has_forbidden_word() -> None:
    assert content.has_forbidden_word("хочешь купить дёшево?", ["купить"])
    assert not content.has_forbidden_word("привет, как дела", ["купить"])


def test_is_acceptable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "content_block_links", True)
    monkeypatch.setattr(settings.warming, "content_forbidden_words", ["реклама"])
    assert content.is_acceptable("привет, как сам?")
    assert not content.is_acceptable("это реклама канала")
    assert not content.is_acceptable("посмотри https://spam.example")


@pytest.mark.asyncio
async def test_try_reserve_sent_first_wins_second_loses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "content_dedup_window_days", 7)
    assert await content.try_reserve_sent("hi there") is True
    assert await content.try_reserve_sent("hi there") is False
    # Normalised collision: punctuation/case-different but same hash → also loses.
    assert await content.try_reserve_sent("Hi, there!") is False


@pytest.mark.asyncio
async def test_try_reserve_sent_zero_window_always_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "content_dedup_window_days", 0)
    assert await content.try_reserve_sent("hi") is True
    assert await content.try_reserve_sent("hi") is True
