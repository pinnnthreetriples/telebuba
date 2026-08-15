"""Expanding one topic into search keywords: what the parser keeps, and why it refuses.

Its own module rather than a tail on ``test_discovery_run.py`` (already ~500 of the
700-line cap), and its own file because it shares nothing with the run tests: no
database, no account, no Telegram — one settings read and one LLM seam.

The parser is where the value is. The model is told "one phrase per line, no
numbering, no quotes, no @" and reliably does some of that, so the rules that decide
whether the operator can post the answer straight back as
``DiscoverySearchRequest.keywords`` have to live on our side of the wire. The three
error codes are the other half: they are the only thing distinguishing "we never
asked" from "the gateway refused" from "the model said nothing usable", and the SPA
shows a different sentence for each.

The seam is patched on ``services.neurocomment._seams``, the module that owns the
binding — never on ``_discovery_keywords``, which merely reads it through the package
(see ``.mex/patterns/add-telegram-task.md`` point 7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from schemas.gemini import GeminiResult
from schemas.neurocomment_discovery import (
    KEYWORD_MAX_LENGTH,
    MAX_KEYWORDS,
    DiscoverySearchRequest,
)
from schemas.neurocomment_discovery_keywords import DiscoveryKeywordRequest
from services.neurocomment import _seams
from services.neurocomment._discovery_keywords import expand_discovery_keywords, parse_keywords

if TYPE_CHECKING:
    from schemas.gemini import GeminiRequest

# One realistically bad answer, carrying every formatting instruction the model was
# given and then ignored — numbering, quotes, an @handle, a comma-separated run, a
# case duplicate, two phrases where single words were asked for — plus the two rules
# it cannot know about (the 4-character floor and our cap).
_MESSY_ANSWER = """1. драки
2) "мордобой"
3. @streetfightclub
* ММ
хулиганы, потасовка
ДРАКИ
бои без правил
самооборона
единоборства
уличные бои
рукопашка
файтинг
кулаки
разборки
махач"""

_CLEAN = [
    "драки",
    "мордобой",
    "streetfightclub",
    "хулиганы",
    "потасовка",
    "самооборона",
    "единоборства",
    "рукопашка",
    "файтинг",
    "кулаки",
]


class _CapturingGateway:
    """Answers with a fixed result and keeps every request it was handed."""

    def __init__(self, result: GeminiResult) -> None:
        self.result = result
        self.requests: list[GeminiRequest] = []

    async def generate_text_deepseek(self, request: GeminiRequest) -> GeminiResult:
        self.requests.append(request)
        return self.result


async def _never_called(_request: GeminiRequest) -> GeminiResult:
    msg = "the LLM gateway was called, but this case must not reach the network"
    raise AssertionError(msg)


def _patch_gateway(monkeypatch: pytest.MonkeyPatch, result: GeminiResult) -> _CapturingGateway:
    gateway = _CapturingGateway(result)
    monkeypatch.setattr(_seams, "generate_text_deepseek", gateway.generate_text_deepseek)
    return gateway


def test_the_parser_cleans_up_everything_the_model_got_wrong() -> None:
    """Numbering, quotes, @handle, too-short line, case duplicate, phrases, the cap.

    Asserted as one exact list rather than a bag of ``in`` checks: the order is the
    model's ranking and the operator reads it top-down, and an assertion per rule
    would keep passing if a later rule started eating earlier rules' output.
    """
    assert parse_keywords(_MESSY_ANSWER) == _CLEAN


def test_a_multi_word_line_is_dropped_whole_and_never_reduced_to_a_fragment() -> None:
    """The SPA splits keywords on whitespace, so a phrase cannot survive into a search.

    Pinned separately from the messy answer above because the failure that matters is
    not "the phrase was kept" — it is the plausible-looking repair where the line is
    split and its longest word kept. That would put "правил" in front of the operator
    as if the model had suggested it, and spend one of the run's ~30 Telegram reads on
    it the moment they accept the list.
    """
    result = parse_keywords("драки\nбои без правил\nмордобой")

    assert result == ["драки", "мордобой"]
    # Named outright, because this is the exact string the tempting repair produces.
    assert "правил" not in result


def test_a_preamble_line_never_becomes_a_keyword() -> None:
    """A preamble like "Вот список слов:" passes every length rule, and costs a read.

    A preamble is short, unique and well inside the bounds, so nothing else here
    stops it — and the operator accepting the list as offered then spends one of the
    run's ~30 reads searching for it and gets a junk row on the board.

    Three shapes, because the guard reads the STRIPPED form and would be trivially
    evaded otherwise: bare, quoted, and numbered. The assertion is the exact list, so
    it fails both ways — a preamble that survives and a real phrase eaten alongside
    it, which is the failure mode any broader heuristic would have introduced.
    """
    answer = (
        "Вот список слов:\n"
        "1. драки\n"
        '2. "мордобой"\n'
        '"Ещё варианты:"\n'
        "3. И напоследок:\n"
        "- хулиганы\n"
        "- потасовка"
    )

    assert parse_keywords(answer) == ["драки", "мордобой", "хулиганы", "потасовка"]


def test_the_parsed_keywords_are_accepted_by_the_search_request_itself() -> None:
    """The whole point of the rules: the answer can be posted back unchanged.

    Re-running the real validator, not a copy of it, so the two cannot drift apart —
    the parser exists to satisfy exactly this model.
    """
    assert DiscoverySearchRequest(keywords=parse_keywords(_MESSY_ANSWER)).keywords == _CLEAN


@pytest.mark.asyncio
async def test_an_empty_deepseek_key_is_reported_without_calling_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No key means no call at all — not a call that fails.

    The gateway stub raises rather than returning, because "returned llm_unavailable"
    would also be true of a version that asked DeepSeek with an empty key and read
    the 401 back. Those differ in a real HTTP round trip per keystroke.
    """
    monkeypatch.setattr(settings.deepseek, "api_key", "")
    monkeypatch.setattr(_seams, "generate_text_deepseek", _never_called)

    result = await expand_discovery_keywords(DiscoveryKeywordRequest(topic="уличные драки"))

    assert result.error == "llm_unavailable"
    assert result.keywords == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        GeminiResult(status="error", error="HTTP 500: upstream"),
        GeminiResult(status="rate_limited", error="HTTP 429: slow down"),
        # 200 with nothing in it: a safety block or an empty choices list. Same code,
        # because the model said nothing about the topic in all three.
        GeminiResult(status="ok", text=None),
    ],
    ids=["error", "rate_limited", "no_text"],
)
async def test_a_gateway_that_does_not_answer_is_llm_failed(
    monkeypatch: pytest.MonkeyPatch,
    answer: GeminiResult,
) -> None:
    monkeypatch.setattr(settings.deepseek, "api_key", "ds-key")
    _patch_gateway(monkeypatch, answer)

    result = await expand_discovery_keywords(DiscoveryKeywordRequest(topic="уличные драки"))

    assert result.error == "llm_failed"
    assert result.keywords == []


@pytest.mark.asyncio
async def test_an_answer_where_nothing_survives_validation_is_llm_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinct from ``llm_failed``: the model DID answer, so a retry buys nothing."""
    monkeypatch.setattr(settings.deepseek, "api_key", "ds-key")
    unusable = "ММ\n- @ты\n* к\n" + "я" * (KEYWORD_MAX_LENGTH + 1)
    _patch_gateway(monkeypatch, GeminiResult(status="ok", text=unusable))

    result = await expand_discovery_keywords(DiscoveryKeywordRequest(topic="уличные драки"))

    assert result.error == "llm_empty"
    assert result.keywords == []


@pytest.mark.asyncio
async def test_the_happy_path_asks_deepseek_with_thinking_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request DeepSeek receives, including the trap that would fail every call.

    ``thinking_budget`` at 0 is what makes ``max_output_tokens`` a budget for the
    answer: V4 reasons by default and bills the thoughts to the same allowance, so an
    enabled one returns ``finish_reason: "length"`` — which ``core.openai`` reports as
    an error, i.e. every expansion would come back ``llm_failed``.
    """
    monkeypatch.setattr(settings.deepseek, "api_key", "ds-key")
    monkeypatch.setattr(settings.deepseek, "model", "deepseek-v4-flash")
    gateway = _patch_gateway(monkeypatch, GeminiResult(status="ok", text=_MESSY_ANSWER))

    result = await expand_discovery_keywords(DiscoveryKeywordRequest(topic="уличные драки"))

    assert result.error is None
    assert result.keywords == _CLEAN
    assert len(gateway.requests) == 1
    request = gateway.requests[0]
    assert request.api_key == "ds-key"
    assert request.model == "deepseek-v4-flash"
    assert request.thinking_budget == 0
    # Room for a full answer, which the comment budget (256, sized for 30 words) is not.
    assert request.max_output_tokens >= MAX_KEYWORDS * KEYWORD_MAX_LENGTH
    # The operator's topic reaches the model, and the instruction names the language
    # and the shape we then parse for. The single-word demand is asserted because the
    # parser now DROPS anything else: an instruction that quietly went back to asking
    # for phrases would cost most of the answer without failing anything else here.
    assert "уличные драки" in request.prompt
    assert "Russian" in request.prompt
    assert "ONE SINGLE WORD" in request.prompt
    assert "one word per line" in request.prompt
