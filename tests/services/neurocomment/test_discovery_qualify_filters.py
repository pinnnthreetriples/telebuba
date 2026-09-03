"""Discovery stage 2 — the operator's probe-time filters: comments, access, language, category.

Its own module because ``test_discovery_qualify`` sits near the 700-line test cap. A
row a filter refuses is DELETED, and the run report counts the drop under the filter's
name; a row the filter admits is qualified exactly as before.
"""

from __future__ import annotations

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
from tests.services.neurocomment.discovery_support import (
    ReadRecorder,
    new_campaign,
    pool_of,
    search_request,
)

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

    reason = await run_qualification(campaign_id, pool_of(), search_request(language="en"))

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

    await run_qualification(campaign_id, pool_of(), search_request(comments=comments))

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

    await run_qualification(campaign_id, pool_of(), search_request(comments="on"))

    assert reader.calls == []
    assert await _remaining(campaign_id) == []
    assert _discovery_state.run_report(campaign_id).filtered == {"comments": 1}


@pytest.mark.asyncio
async def test_a_language_or_category_filter_skips_the_cache_shortcut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Those two need the ``about`` text, which only the probe carries."""
    reader = ReadRecorder(linked=_reply(about="Daily news"))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _seed(("cached", "Cached"))
    await upsert_linked_group("cached", -100, comments_enabled=True)

    await run_qualification(campaign_id, pool_of(), search_request(language="en"))

    assert len(reader.actions_of("get_linked_discussion_group")) == 1
    assert await _remaining(campaign_id) == ["cached"]


@pytest.mark.asyncio
async def test_a_private_row_is_never_probed_and_reads_as_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = ReadRecorder()
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _seed(("id:123456", "Private"))

    reason = await run_qualification(campaign_id, pool_of(), search_request())

    assert reason is None
    assert reader.calls == []
    rows = (await list_discovery_candidates(campaign_id)).rows
    assert rows[0].qualified_at is not None
    assert _discovery_state.verdicts(campaign_id)["id:123456"].access == "subscription"


@pytest.mark.asyncio
async def test_a_target_join_gate_reads_as_join_request_and_the_access_filter_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _seams, "execute_read", ReadRecorder(linked=_reply(target_join_request=True))
    )
    campaign_id = await _seed(("gated", "Gated"))

    await run_qualification(campaign_id, pool_of(), search_request(access="open"))

    assert await _remaining(campaign_id) == []
    assert _discovery_state.run_report(campaign_id).filtered == {"access": 1}
    assert _discovery_state.verdicts(campaign_id)["gated"].access == "join_request"


@pytest.mark.asyncio
async def test_category_match_is_measured_and_a_miss_deletes_the_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(linked=_reply(about="Cats and dogs")))
    campaign_id = await _seed(("btcdaily", "Bitcoin daily"), ("pets", "Pets"))

    await run_qualification(campaign_id, pool_of(), search_request(category="crypto"))

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

    await run_qualification(campaign_id, pool_of(), search_request(kind="all", language="ru"))

    # Digits only: no language could be read, so the language filter lets it through.
    assert await _remaining(campaign_id) == ["chat"]
    verdict = _discovery_state.verdicts(campaign_id)["chat"]
    assert verdict.is_group is True
    assert verdict.language is None
