"""Keyword-suggestion schemas — split from ``schemas.neurocomment_discovery`` (size cap).

Same one-way arrangement that module documents for its own split from
``schemas.neurocomment``: this one imports the discovery bounds, and that one must
never import this. Keyword suggestion is a convenience beside the search box rather
than a stage of a run, so nothing in the run's own contracts refers to it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from schemas.neurocomment_discovery import KEYWORD_MAX_LENGTH, MAX_KEYWORDS


class DiscoveryKeywordRequest(BaseModel):
    """One operator-typed topic, to be expanded into a keyword list by the LLM.

    Deliberately NOT campaign-scoped: expanding a topic reads no campaign state and
    spends no Telegram budget, so taking a campaign id would only invite the caller
    to believe it changes the answer.

    ``topic`` is bounded by ``KEYWORD_MAX_LENGTH`` rather than by a length of its
    own: it is the same kind of string the operator would otherwise have typed into
    ``DiscoverySearchRequest.keywords`` by hand, and it is interpolated into a
    prompt — an unbounded topic would be an unbounded prompt. Only ``min_length=1``
    below, not the keyword floor: a 2-character topic ("MMA") is a perfectly good
    thing to expand even though it is too short to search Telegram with, which is
    exactly why the operator is asking for an expansion.
    """

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=KEYWORD_MAX_LENGTH)


class DiscoveryKeywordResult(BaseModel):
    """The expanded keywords, or the short code saying why there are none.

    ``keywords`` come out ready to be posted straight back as
    ``DiscoverySearchRequest.keywords``: stripped, deduped case-insensitively,
    within ``KEYWORD_MIN_LENGTH``..``KEYWORD_MAX_LENGTH`` and capped at
    ``MAX_KEYWORDS``. The service applies those rules itself rather than trusting
    the model, so an answer the operator accepts wholesale cannot fail the search
    request's own validator.

    They are also single words, which is a tighter rule than that schema's — the
    search request happily takes a keyword containing spaces, and a hand-typed one
    still may. The suggestions cannot, because the SPA's keywords field splits what
    it receives on whitespace as well as commas, so a multi-word suggestion would
    reach the search as fragments of itself.

    ``error`` is a short locale-neutral code, exactly like
    ``DiscoverySourceReport.reason`` — the SPA maps it to text and renders the raw
    code when it has no copy. Three values, and each names a different thing for the
    operator to do:
      llm_unavailable — ``settings.deepseek.api_key`` is empty, so nothing was asked
                        at all; type the keywords by hand or set the key.
      llm_failed      — the gateway answered with an error, a rate limit, or no
                        text; retrying may work.
      llm_empty       — the model answered but nothing in it survived validation;
                        rephrasing the topic is what helps.
    ``str`` rather than a ``Literal`` for the same reason as ``reason``: the SPA
    already falls back to the raw code, so widening the set later must not be a
    breaking change to the generated client.

    A non-null ``error`` always carries an empty ``keywords`` list — there is no
    partial answer to report, since a model that produced anything usable is a
    success.
    """

    keywords: list[str] = Field(default_factory=list, max_length=MAX_KEYWORDS)
    error: str | None = None
