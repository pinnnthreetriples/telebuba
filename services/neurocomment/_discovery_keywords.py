"""Expand one operator-typed topic into Telegram search keywords with the LLM.

A convenience over the keyword box on the discovery board, not part of a run: it
touches no campaign, reserves no account and spends no Telegram budget, so it lives
beside the discovery modules rather than inside ``discovery.py``'s run machinery.

DeepSeek only. ``_llm._deepseek_generates`` falls back to Gemini when the deployment
has no DeepSeek key, and that is right for the comment hot path — but the Gemini key
is a per-campaign secret (``WarmingSettingsSecret.gemini_api_key``), and this route
has no campaign to read one from. Reaching for a campaign's key here would drag
campaign state into a request that deliberately has none, so an unset DeepSeek key is
simply reported (``llm_unavailable``) and nothing is called.

The parser assumes the model ignores half the formatting instruction, because it
does. Every line is filtered by the rules ``DiscoverySearchRequest`` applies to
hand-typed keywords, so whatever the operator accepts is guaranteed to survive that
validator; anything failing is dropped in silence, since one bad line must not cost
the operator the other nine.
"""

from __future__ import annotations

import re

from core.config import settings
from schemas.gemini import GeminiRequest
from schemas.neurocomment_discovery import (
    KEYWORD_MAX_LENGTH,
    KEYWORD_MIN_LENGTH,
    MAX_KEYWORDS,
    DiscoveryKeywordRequest,
    DiscoveryKeywordResult,
)
from services.neurocomment import _seams

# Leading list furniture: a bullet of any of the usual shapes, or "1." / "2)" —
# stripped before the edge pass so a numbered, quoted line ends up bare. The two
# dashes are escaped rather than written literally: en/em dash are what a Russian
# answer bullets with, and ruff's RUF001 refuses them next to a plain hyphen.
_LEADING_MARKER = re.compile(r"^(?:[-*•·\u2013\u2014>]+|\d+\s*[.)])\s*")
# Quotes of every dialect the model reaches for, plus a leading ``@``: a handle is a
# fine search phrase once it stops pretending to be a mention.
_EDGE_NOISE = re.compile(r"^[\s\"'«“„`@]+|[\s\"'»”`]+$")

# The model is asked for one word per line and often obliges with a comma-separated
# run instead. Both separators, one split.
_SEPARATORS = re.compile(r"[\n,]")

# Any whitespace left INSIDE a candidate once the edges are cleaned — i.e. the model
# answered with a phrase where a single word was asked for.
_INNER_SPACE = re.compile(r"\s")

# Ceiling for the whole answer. ``settings.deepseek.max_output_tokens`` is the
# comment budget (256, sized for a 30-word reply) and this asks for ten entries, so
# borrowing it would truncate a full answer — and a DeepSeek cut arrives as
# ``finish_reason: "length"``, which ``core.openai`` reports as an error rather than
# a short success, i.e. the whole call would fail. One token per character is well
# past the pessimistic Cyrillic rate, so the largest answer we would KEEP
# (``MAX_KEYWORDS`` lines of ``KEYWORD_MAX_LENGTH``, plus separators) fits with room
# to spare, and stays under ``GeminiRequest.max_output_tokens``' own 2048 ceiling.
_MAX_OUTPUT_TOKENS = MAX_KEYWORDS * (KEYWORD_MAX_LENGTH + 1)

# Single words, not phrases — a constraint of the CLIENT, not of the API. The search
# request itself accepts a keyword containing spaces, but the SPA's keywords field
# tokenises what it is handed on whitespace as well as commas (``splitKeywords``), so
# a suggested "бои без правил" arrives as three keywords of which only one clears
# ``KEYWORD_MIN_LENGTH`` — a word nobody suggested, spending a real Telegram read.
# Stated three ways because a model that reads "phrase" once keeps writing phrases,
# and shown by example because ``KEYWORD_MIN_LENGTH`` is 4: a lot of the obvious
# Russian answers are single words already, and the example is what stops the model
# reaching for a two-word one to pad the length.
_PROMPT = (
    "The operator is looking for Telegram channels about this topic: {topic}\n\n"
    "List up to {max_keywords} Russian-language search WORDS a Telegram user would "
    "type to find channels on that topic: synonyms, colloquial forms and adjacent "
    "subtopics.\n"
    "Every entry must be ONE SINGLE WORD containing no spaces. Not a phrase, not two "
    "words joined by a space — one word. Any line containing a space is discarded.\n"
    "Each word must be between {min_length} and {max_length} characters.\n"
    'Example — for the topic "уличные драки", good entries are: драки, мордобой, '
    'хулиганы, самооборона. Rejected entries: "бои без правил", "уличные драки", '
    '"драки на улице" — all contain spaces.\n'
    "Answer with a plain list, one word per line. No numbering, no hashtags, no @, "
    "no quotes, no explanations."
)


def _build_request(topic: str) -> GeminiRequest:
    """Compose the DeepSeek call.

    Not ``_llm._build_request``: that one exists to wrap an untrusted channel post in
    its fence and to pick between the two providers using a campaign's Gemini secret.
    There is no post here, and no campaign — reusing it would mean inventing a
    ``_Subject`` and a ``WarmingSettingsSecret`` for a request that wants neither.
    What IS reused is the shape of its DeepSeek branch, and the provider-neutral
    request type both gateways take.

    ``thinking_budget`` is left at its ``0`` default on purpose, which is what makes
    ``max_output_tokens`` mean "room for the answer": DeepSeek-V4 reasons by default
    and bills the thoughts to the same budget, so an enabled one would spend the
    allowance on thoughts and return the truncation this module has no use for.

    The topic is the operator's own text and rides into the prompt unfenced. They can
    already type any prompt they like into a campaign, so there is no privilege here
    to escalate to — and the blast radius is bounded twice over anyway: 64 characters
    in, and out the other side nothing but short strings that pass the keyword rules.
    """
    return GeminiRequest(
        api_key=settings.deepseek.api_key,
        prompt=_PROMPT.format(
            topic=topic,
            max_keywords=MAX_KEYWORDS,
            min_length=KEYWORD_MIN_LENGTH,
            max_length=KEYWORD_MAX_LENGTH,
        ),
        model=settings.deepseek.model,
        temperature=settings.deepseek.temperature,
        max_output_tokens=_MAX_OUTPUT_TOKENS,
    )


def parse_keywords(text: str) -> list[str]:
    """Turn a model answer into keywords the search request would accept.

    Public because it is the half worth testing on its own: the branch that calls it
    needs a patched gateway, this needs a string.

    Same rules as ``DiscoverySearchRequest._check_bounds``, in the same order —
    strip, bound the STRIPPED length, dedupe case-insensitively, then cap — because
    the point is that the result can be posted straight back there unchanged. The
    difference is only what a violation costs: the operator typing a 2-character
    keyword gets a 422 telling them so, while a model doing it gets the line dropped.

    Plus two rules of its own, for the instructions the model does not reliably
    follow — see the trailing-colon and inner-space checks below.
    """
    keywords: list[str] = []
    seen: set[str] = set()
    for line in _SEPARATORS.split(text):
        stripped = _EDGE_NOISE.sub("", _LEADING_MARKER.sub("", line.strip()))
        if stripped.endswith(":"):
            # "Вот список фраз:" / "Here are 10 phrases:" is short enough to pass every
            # rule above, and a list the operator accepts as-is then spends a real
            # Telegram read out of the run's budget on it and leaves a junk row on the
            # board. Checked on the STRIPPED form so a quoted or numbered preamble is
            # caught too.
            #
            # The trailing colon is the only preamble tell worth acting on: a phrase
            # someone would type into Telegram search does not end in one, so the rule
            # cannot silently eat a real keyword. Every broader idea can — a word-count
            # cap loses "драки без правил на улице", a Cyrillic check loses the English
            # handles we deliberately keep, a phrase blacklist loses whatever wording
            # the next model picks — and each fails INVISIBLY, dropping a good keyword
            # with no more trace than a bad one. That asymmetry is the whole argument:
            # a preamble that slips through is one row the operator can see and
            # deselect, while a keyword wrongly dropped is one they never learn existed.
            continue
        if _INNER_SPACE.search(stripped):
            # The prompt asks for single words; this is what enforces it, because an
            # instruction is not a guarantee. See ``_PROMPT`` for why the SPA cannot
            # carry a multi-word suggestion at all.
            #
            # The whole line goes. Splitting it and keeping the words that clear
            # ``KEYWORD_MIN_LENGTH`` is the tempting alternative and it is the same
            # asymmetry as the colon guard, one step worse: "бои без правил" would
            # become "правил", a word neither the operator nor the model proposed,
            # sitting in the list looking exactly like a real suggestion and spending
            # a real read when accepted. A silently reduced phrase is invisible; a
            # dropped line is merely one suggestion fewer, out of ten asked for.
            continue
        if not (KEYWORD_MIN_LENGTH <= len(stripped) <= KEYWORD_MAX_LENGTH):
            continue
        if stripped.casefold() in seen:
            continue
        seen.add(stripped.casefold())
        keywords.append(stripped)
        if len(keywords) == MAX_KEYWORDS:
            break
    return keywords


async def expand_discovery_keywords(
    request: DiscoveryKeywordRequest,
) -> DiscoveryKeywordResult:
    """Ask DeepSeek to widen ``request.topic`` into a search-ready keyword list."""
    if not settings.deepseek.api_key:
        # Before any request is built, so this branch cannot be mistaken for a call
        # that failed: there is no key to call with, which is a deployment fact the
        # operator can act on rather than an upstream hiccup to retry.
        return DiscoveryKeywordResult(error="llm_unavailable")
    result = await _seams.generate_text_deepseek(_build_request(request.topic))
    if result.status != "ok" or result.text is None:
        # Errors, rate limits and a 200 carrying no text are one code: all three mean
        # the model never spoke, and none of them says anything about the topic.
        return DiscoveryKeywordResult(error="llm_failed")
    keywords = parse_keywords(result.text)
    if not keywords:
        return DiscoveryKeywordResult(error="llm_empty")
    return DiscoveryKeywordResult(keywords=keywords)
