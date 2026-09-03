"""Discovery stage 2 — the operator's probe-time filters: comments, access, language, category.

Its own module because ``test_discovery_qualify`` sits near the 700-line test cap. A
row a filter refuses is DELETED, and the run report counts the drop under the filter's
name; a row the filter admits is qualified exactly as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories.neurocomment import (
    list_discovery_candidates,
    replace_discovery_candidates,
    upsert_linked_group,
)
from schemas.neurocomment_discovery import DiscoveryCandidateRow
from schemas.telegram_actions import LinkedDiscussionGroupResult
from services.neurocomment import _discovery_state, _seams
from services.neurocomment._discovery_qualify import run_qualification
from services.neurocomment.discovery import load_discovery
from tests.services.neurocomment.discovery_support import (
    ReadRecorder,
    new_campaign,
    pool_of,
    search_request,
    work_for,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

pytestmark = pytest.mark.usefixtures("isolate_discovery")


async def _seed(*rows: tuple[str, str]) -> str:
    campaign_id = await new_campaign()
    await replace_discovery_candidates(
        campaign_id,
        [
            DiscoveryCandidateRow(channel=channel, title=title, source="telegram_search")
            for channel, title in rows
        ],
    )
    return campaign_id


async def _remaining(campaign_id: str) -> list[str]:
    return [row.channel for row in (await list_discovery_candidates(campaign_id)).rows]


def _reply(**fields: object) -> LinkedDiscussionGroupResult:
    return LinkedDiscussionGroupResult.model_validate(
        {"linked_chat_id": -100, "comments_enabled": True, **fields},
    )


@pytest.mark.asyncio
async def test_a_language_mismatch_deletes_the_row_and_is_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Title + about, so a terse Latin title with a Russian about still reads as Russian."""
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(linked=_reply(about="Ежедневные новости о криптовалютах и рынке")),
    )
    campaign_id = await _seed(("cryptoru", "BTC"))

    reason = await run_qualification(
        campaign_id, pool_of(), search_request(language="en"), work_for(pool_of())
    )

    assert reason is None
    assert await _remaining(campaign_id) == []
    assert _discovery_state.run_report(campaign_id).filtered == {"language": 1}
    # The verdict is still recorded, so the board can say what the probe saw.
    assert _discovery_state.verdicts(campaign_id)["cryptoru"].language == "ru"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("comments", "enabled", "kept"),
    [("on", False, False), ("off", True, False), ("on", True, True), ("any", False, True)],
)
async def test_the_comments_filter_drops_the_wrong_verdict(
    monkeypatch: pytest.MonkeyPatch,
    *,
    comments: str,
    enabled: bool,
    kept: bool,
) -> None:
    reply = _reply(comments_enabled=enabled, linked_chat_id=-100 if enabled else None)
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(linked=reply))
    campaign_id = await _seed(("alpha", "Alpha"))

    await run_qualification(
        campaign_id, pool_of(), search_request(comments=comments), work_for(pool_of())
    )

    assert (await _remaining(campaign_id) == ["alpha"]) is kept


@pytest.mark.asyncio
async def test_a_cache_hit_still_honours_the_comments_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No probe is spent, and the cached verdict is what the filter reads."""
    reader = ReadRecorder()
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _seed(("cached", "Cached"))
    await upsert_linked_group("cached", None, comments_enabled=False)

    await run_qualification(
        campaign_id, pool_of(), search_request(comments="on"), work_for(pool_of())
    )

    assert reader.calls == []
    assert await _remaining(campaign_id) == []
    assert _discovery_state.run_report(campaign_id).filtered == {"comments": 1}


@pytest.mark.asyncio
async def test_an_unknown_kind_is_not_a_channel_for_the_comments_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A legacy or blank ``kind`` read as a confident "channel", and a cached False deleted it."""
    reader = ReadRecorder()
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await new_campaign()
    await replace_discovery_candidates(
        campaign_id,
        [DiscoveryCandidateRow(channel="odd", title="Odd", source="telegram_search", kind="")],
    )
    await upsert_linked_group("odd", None, comments_enabled=False)

    await run_qualification(
        campaign_id, pool_of(), search_request(comments="on"), work_for(pool_of())
    )

    assert reader.calls == []
    assert await _remaining(campaign_id) == ["odd"]
    assert _discovery_state.verdicts(campaign_id)["odd"].is_group is None


@pytest.mark.asyncio
async def test_a_cache_row_that_carries_about_and_the_join_gate_settles_every_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No RPC, and the language, category AND access filters read the cached facts.

    Before #61 the cache answered comments only: a language or category filter bypassed
    it wholesale (one probe per candidate on every re-search), and a cache hit skipped the
    access filter altogether, so ``access=open`` let a join-gated channel through.
    """
    reader = ReadRecorder()
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _seed(("ruchan", "BTC"), ("gated", "Crypto daily"), ("fit", "Crypto"))
    await upsert_linked_group(
        "ruchan", -1, comments_enabled=True, about="Новости", join_request=False
    )
    await upsert_linked_group("gated", -1, comments_enabled=True, about="", join_request=True)
    await upsert_linked_group("fit", -1, comments_enabled=True, about="", join_request=False)

    await run_qualification(
        campaign_id, pool_of(), search_request(language="en", access="open"), work_for(pool_of())
    )

    assert reader.calls == []
    assert await _remaining(campaign_id) == ["fit"]
    assert _discovery_state.run_report(campaign_id).filtered == {"language": 1, "access": 1}


@pytest.mark.asyncio
async def test_a_cache_settled_row_shows_its_facts_on_the_board(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The board lifts access, language and the category match off the verdict.

    A row the cache settled derived all three and filtered on them, but recorded no
    verdict — so the cheap path showed every one of them as unknown.
    """
    reader = ReadRecorder()
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _seed(("btcdaily", "Bitcoin daily"))
    await upsert_linked_group(
        "btcdaily", -1, comments_enabled=True, about="Crypto news", join_request=False
    )

    await run_qualification(
        campaign_id, pool_of(), search_request(category="crypto"), work_for(pool_of())
    )

    assert reader.calls == []
    board = await load_discovery(campaign_id)
    assert board is not None
    row = board.candidates[0]
    assert (row.access, row.language, row.category_match) == ("open", "en", True)
    # Nothing probed the writing rights this run, so they stay unknown — not fine.
    assert row.verdict is not None
    assert (row.verdict.can_send_messages, row.verdict.is_group) == (None, False)


@pytest.mark.asyncio
async def test_a_legacy_cache_row_without_the_needed_fact_is_probed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-#61 row has NULL where about should be — never learnt, so the probe pays."""
    reader = ReadRecorder(linked=_reply(about="Daily news"))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _seed(("cached", "Cached"), ("plain", "Plain"))
    await upsert_linked_group("cached", -100, comments_enabled=True)
    await upsert_linked_group("plain", -100, comments_enabled=True)

    await run_qualification(
        campaign_id, pool_of(), search_request(language="en"), work_for(pool_of())
    )

    # Both rows lack the about text, both are probed; the reply refreshes the cache.
    assert len(reader.actions_of("get_linked_discussion_group")) == 2
    assert await _remaining(campaign_id) == ["cached", "plain"]


@pytest.mark.asyncio
async def test_a_private_row_is_never_probed_and_reads_as_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = ReadRecorder()
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _seed(("id:123456", "Private"))

    reason = await run_qualification(campaign_id, pool_of(), search_request(), work_for(pool_of()))

    assert reason is None
    assert reader.calls == []
    rows = (await list_discovery_candidates(campaign_id)).rows
    assert rows[0].qualified_at is not None
    verdict = _discovery_state.verdicts(campaign_id)["id:123456"]
    assert (verdict.access, verdict.language) == ("subscription", "en")


@pytest.mark.asyncio
async def test_a_private_row_goes_through_the_filters_on_its_title_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``id:`` rows used to bypass every qualification filter.

    Language and category read the title; ``comments=on`` refuses by rule — a channel
    nobody can probe or comment in can never satisfy "has comments".
    """
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder())
    by_language = await _seed(("id:1", "Новости"), ("id:2", "Crypto news"))
    by_comments = await new_campaign()
    await replace_discovery_candidates(
        by_comments,
        [
            DiscoveryCandidateRow(channel="id:2", title="Crypto news", source="telegram_similar"),
            DiscoveryCandidateRow(
                channel="id:3", title="Crypto", source="telegram_similar", kind="group"
            ),
        ],
    )

    await run_qualification(
        by_language, pool_of(), search_request(language="en"), work_for(pool_of())
    )
    await run_qualification(
        by_comments, pool_of(), search_request(comments="on"), work_for(pool_of())
    )

    assert await _remaining(by_language) == ["id:2"]
    assert _discovery_state.run_report(by_language).filtered == {"language": 1}
    # The private channel fails the comments rule; the private group skips that leg.
    assert await _remaining(by_comments) == ["id:3"]
    assert _discovery_state.run_report(by_comments).filtered == {"comments": 1}


@pytest.mark.asyncio
async def test_a_group_is_kept_whatever_the_comments_filter_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``kind=all`` + ``comments=on`` deleted every group: a megagroup has no linked chat."""
    reply = _reply(comments_enabled=False, linked_chat_id=None, is_group=True)
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(linked=reply))
    campaign_id = await new_campaign()
    await replace_discovery_candidates(
        campaign_id,
        [
            DiscoveryCandidateRow(
                channel="chat", title="Chat", source="telegram_search", kind="group"
            )
        ],
    )

    await run_qualification(
        campaign_id, pool_of(), search_request(kind="all", comments="on"), work_for(pool_of())
    )

    assert await _remaining(campaign_id) == ["chat"]
    assert _discovery_state.run_report(campaign_id).filtered == {}


@pytest.mark.asyncio
async def test_rejected_rows_are_deleted_in_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    """One DELETE at the progress tick and one at the end, not one per rejected row."""
    from services.neurocomment import _discovery_qualify  # noqa: PLC0415

    deletes: list[list[str]] = []
    real_delete = _discovery_qualify.delete_discovery_candidates

    async def _counting(campaign_id: str, channels: Iterable[str]) -> None:
        listed = list(channels)
        deletes.append(listed)
        await real_delete(campaign_id, listed)

    monkeypatch.setattr(_discovery_qualify, "delete_discovery_candidates", _counting)
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(linked=_reply(about="Новости")))
    campaign_id = await _seed(*((f"c{index}", "BTC") for index in range(7)))

    await run_qualification(
        campaign_id, pool_of(), search_request(language="en"), work_for(pool_of())
    )

    assert await _remaining(campaign_id) == []
    assert [len(batch) for batch in deletes] == [5, 2]


@pytest.mark.asyncio
async def test_a_target_join_gate_reads_as_join_request_and_the_access_filter_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _seams, "execute_read", ReadRecorder(linked=_reply(target_join_request=True))
    )
    campaign_id = await _seed(("gated", "Gated"))

    await run_qualification(
        campaign_id, pool_of(), search_request(access="open"), work_for(pool_of())
    )

    assert await _remaining(campaign_id) == []
    assert _discovery_state.run_report(campaign_id).filtered == {"access": 1}
    assert _discovery_state.verdicts(campaign_id)["gated"].access == "join_request"


@pytest.mark.asyncio
async def test_category_match_is_measured_and_a_miss_deletes_the_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(linked=_reply(about="Cats and dogs")))
    campaign_id = await _seed(("btcdaily", "Bitcoin daily"), ("pets", "Pets"))

    await run_qualification(
        campaign_id, pool_of(), search_request(category="crypto"), work_for(pool_of())
    )

    assert await _remaining(campaign_id) == ["btcdaily"]
    verdicts = _discovery_state.verdicts(campaign_id)
    assert verdicts["btcdaily"].category_match is True
    assert verdicts["pets"].category_match is False
    assert _discovery_state.run_report(campaign_id).filtered == {"category": 1}


@pytest.mark.asyncio
async def test_the_verdict_carries_is_group_and_no_filter_touches_an_unknown_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``None`` from the probe never rejects: a filter refuses only on a fact it has."""
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(linked=_reply(is_group=True)))
    campaign_id = await _seed(("chat", "12345"))

    await run_qualification(
        campaign_id, pool_of(), search_request(kind="all", language="ru"), work_for(pool_of())
    )

    # Digits only: no language could be read, so the language filter lets it through.
    assert await _remaining(campaign_id) == ["chat"]
    verdict = _discovery_state.verdicts(campaign_id)["chat"]
    assert verdict.is_group is True
    assert verdict.language is None
