"""«Найти каналы» (item 4.5): search with a non-warming account, filter, propose, approve.

The gateway reads are faked at the warming seam; no real account is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.db import create_account, list_warming_channels, update_account_from_session_check
from core.repositories.warming_pool import list_pending_warming_channels
from schemas._warming_pool import ChannelBatchRequest, PoolFillRequest
from schemas.accounts import AccountCreate
from schemas.neurocomment_discovery_request import DiscoveryAccountList, DiscoveryAccountOption
from schemas.telegram_action_results import LinkedDiscussionGroupResult
from schemas.telegram_actions_activity import LastPostResult
from schemas.telegram_actions_discovery import TelegramChannelMatch, TelegramChannelMatches
from schemas.telegram_session import TelegramSessionCheckResult
from schemas.warming import AddChannelsRequest
from services import warming
from services.warming import _seams, pool_fill

_NOW = datetime.now(UTC)
# handle -> (title, subscribers, about, days since the last post)
_CHANNELS: dict[str, tuple[str, int, str, int]] = {
    "futbol_mx": ("Fútbol de México hoy", 40_000, "noticias del fútbol mexicano", 1),
    "tiny_mx": ("Recetas de la abuela", 300, "recetas caseras", 1),
    "huge_mx": ("Noticias nacionales", 2_000_000, "todas las noticias", 1),
    "casino_mx": ("Casino ganancias", 50_000, "apuestas", 1),
    "dead_mx": ("Cocina mexicana", 9_000, "recetas tradicionales", 90),
    "ru_news": ("Новости спорта России", 30_000, "все о спорте в России", 1),
    "cine_mx": ("Cine y series", 15_000, "estrenos de la semana", 2),
}


async def _account(account_id: str) -> None:
    await create_account(AccountCreate(account_id=account_id))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id=account_id,
            session_path=account_id,
            status="alive",
            is_temporary=False,
            user_id=1,
        ),
    )


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []

    async def options() -> DiscoveryAccountList:
        return DiscoveryAccountList(
            items=[
                DiscoveryAccountOption(account_id="searcher", name="s"),
                DiscoveryAccountOption(account_id="busy", name="b", busy_reason="account_busy"),
            ]
        )

    async def execute_read(account_id: str, action: object) -> object:
        kind = getattr(action, "action_type", "")
        seen.append((account_id, kind))
        if kind == "search_channels":
            return TelegramChannelMatches(
                items=[
                    TelegramChannelMatch(username=h, title=t[0], kind="channel")
                    for h, t in _CHANNELS.items()
                    if h != "cine_mx"
                ]
            )
        if kind == "get_similar_channels":
            return TelegramChannelMatches(
                items=[TelegramChannelMatch(username="cine_mx", title="Cine y series")]
            )
        handle = getattr(action, "channel", "@").lstrip("@")
        _title, subscribers, about, days = _CHANNELS[handle]
        if kind == "get_linked_discussion_group":
            return LinkedDiscussionGroupResult(
                comments_enabled=False, participants_count=subscribers, about=about
            )
        return LastPostResult(last_post_at=(_NOW - timedelta(days=days)).isoformat())

    monkeypatch.setattr(pool_fill, "list_search_accounts", options)
    monkeypatch.setattr(_seams, "execute_read", execute_read)
    return seen


@pytest.mark.asyncio
async def test_only_fitting_channels_are_proposed_and_none_is_walked(
    reads: list[tuple[str, str]],
) -> None:
    await _account("searcher")
    await warming.add_channels(AddChannelsRequest(raw="@cine_mx"))

    result = await pool_fill.find_channels(
        PoolFillRequest(language="es", country="MX", topics=["fútbol"])
    )

    pending = {c.channel: c for c in (await list_pending_warming_channels()).channels}
    assert set(pending) == {"futbol_mx"}
    proposal = pending["futbol_mx"]
    assert (proposal.status, proposal.source, proposal.language, proposal.country) == (
        "pending",
        "discovery",
        "es",
        "MX",
    )
    assert proposal.subscribers == 40_000
    assert result.status == "ok"
    assert result.proposed == 1
    # The search read only with the idle non-warming account.
    assert {account for account, _kind in reads} == {"searcher"}
    # The walked pool still has only the operator's own channel.
    assert [c.channel for c in (await list_warming_channels()).channels] == ["cine_mx"]


@pytest.mark.asyncio
async def test_approval_moves_proposals_into_the_pool(reads: list[tuple[str, str]]) -> None:
    del reads
    await _account("searcher")
    await pool_fill.find_channels(PoolFillRequest(language="es", topics=["fútbol"]))

    waiting = await pool_fill.approve_proposals(ChannelBatchRequest(channels=["futbol_mx"]))

    # The similar-channel find stays waiting; only the ticked one is walked now.
    assert [c.channel for c in waiting.channels] == ["cine_mx"]
    walked = (await list_warming_channels()).channels
    assert [(c.channel, c.status) for c in walked] == [("futbol_mx", "approved")]


@pytest.mark.asyncio
async def test_rejection_drops_only_proposals(reads: list[tuple[str, str]]) -> None:
    del reads
    await _account("searcher")
    await warming.add_channels(AddChannelsRequest(raw="@mine"))
    await pool_fill.find_channels(PoolFillRequest(language="es", topics=["fútbol"]))

    await pool_fill.reject_proposals(ChannelBatchRequest(channels=["futbol_mx", "cine_mx", "mine"]))

    assert (await list_pending_warming_channels()).channels == []
    assert [c.channel for c in (await list_warming_channels()).channels] == ["mine"]


@pytest.mark.asyncio
async def test_typing_a_proposed_channel_approves_it(reads: list[tuple[str, str]]) -> None:
    del reads
    await _account("searcher")
    await pool_fill.find_channels(PoolFillRequest(language="es", topics=["fútbol"]))
    await warming.add_channels(AddChannelsRequest(raw="@futbol_mx"))
    assert [c.channel for c in (await list_warming_channels()).channels] == ["futbol_mx"]


@pytest.mark.asyncio
async def test_no_idle_account_outside_warming_means_no_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def options() -> DiscoveryAccountList:
        return DiscoveryAccountList(
            items=[DiscoveryAccountOption(account_id="w", name="w", busy_reason="account_busy")]
        )

    monkeypatch.setattr(pool_fill, "list_search_accounts", options)
    result = await pool_fill.find_channels(PoolFillRequest(language="es", topics=["fútbol"]))
    assert result.status == "no_search_account"


@pytest.mark.asyncio
async def test_a_case_variant_approves_the_proposal_without_a_duplicate(
    reads: list[tuple[str, str]],
) -> None:
    del reads
    await _account("searcher")
    await pool_fill.find_channels(PoolFillRequest(language="es", topics=["fútbol"]))
    await warming.add_channels(AddChannelsRequest(raw="@Futbol_MX"))
    assert [c.channel for c in (await list_warming_channels()).channels] == ["futbol_mx"]


@pytest.mark.asyncio
async def test_an_account_claimed_before_the_lock_is_not_searched_with(
    reads: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account("searcher")
    answers = iter(
        [
            DiscoveryAccountList(items=[DiscoveryAccountOption(account_id="searcher", name="s")]),
            DiscoveryAccountList(
                items=[
                    DiscoveryAccountOption(
                        account_id="searcher", name="s", busy_reason="account_busy"
                    )
                ]
            ),
        ]
    )

    async def options() -> DiscoveryAccountList:
        return next(answers)

    monkeypatch.setattr(pool_fill, "list_search_accounts", options)
    result = await pool_fill.find_channels(PoolFillRequest(language="es", topics=["fútbol"]))
    assert result.status == "no_search_account"
    assert reads == []
