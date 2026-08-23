"""Board card fields that answer "this account, in THIS channel".

Split out of ``test_neurocomment_board.py``, which sits at the 700-line test cap:
the per-pair deletion count and the last-comment deletion flag both exist because
a board row names one channel per account, so a number or a mark rendered beside
that name has to be about that pair and not about the account as a whole.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    assign_account_to_campaign,
    claim_comment,
    configure_database,
    create_account,
    create_campaign,
    link_channel_to_campaign,
    mark_comment_posted,
    mark_comments_deleted,
    upsert_readiness,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from services.neurocomment import _state
from services.neurocomment.board import load_neurocomment_board

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    _state.reset_for_tests()  # the in-memory channel state is module-global; isolate per test
    reset_logging_for_tests()
    setup_logging()


async def _post_comment(channel: str, post_id: int, campaign_id: str, account_id: str) -> None:
    await claim_comment(channel, post_id, campaign_id, account_id)
    await mark_comment_posted(channel, post_id, comment_text="hi", comment_msg_id=post_id)


async def _seed_pair(channels: tuple[str, ...]) -> str:
    """One account joined to every ``channels`` entry, one posted comment in each."""
    campaign = await create_campaign(CampaignCreate(name="C1", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    for post_id, channel in enumerate(channels, start=1):
        await link_channel_to_campaign(campaign.campaign_id, channel)
        await upsert_readiness("acc-1", channel, joined=True, captcha_passed=True, ready=True)
        await _post_comment(channel, post_id, campaign.campaign_id, "acc-1")
    return campaign.campaign_id


@pytest.mark.asyncio
async def test_card_splits_its_deletions_by_channel() -> None:
    """The chip sits beside ONE channel name, so it counts that pair, not the account."""
    campaign_id = await _seed_pair(("@news", "@old"))
    await mark_comments_deleted("@old", [2])

    board = await load_neurocomment_board(campaign_id)

    assert board is not None
    assert board.accounts[0].deleted_today == 1  # the flat total is silent about WHICH one
    assert {r.channel: r.deleted for r in board.accounts[0].readiness} == {"@news": 0, "@old": 1}


@pytest.mark.asyncio
async def test_last_comment_deleted_follows_the_newest_comment_only() -> None:
    """Both directions, because the row strikes the text through off this one flag.

    A card built from ``any(c.deleted_at ...)`` passes the True half and fails the False
    one; a card built from ``posted[0]`` fails the True half — @old's post 2 is the newest.
    """
    campaign_id = await _seed_pair(("@news", "@old"))
    await mark_comments_deleted("@old", [2])

    board = await load_neurocomment_board(campaign_id)
    assert board is not None
    assert board.accounts[0].last_comment_deleted is True

    # A fresh, live comment in @news now outranks the removed one; the flag must follow it
    # even though the account still has a deletion inside the window.
    await _post_comment("@news", 3, campaign_id, "acc-1")

    board = await load_neurocomment_board(campaign_id)
    assert board is not None
    assert board.accounts[0].deleted_today == 1  # still there, just no longer the newest
    assert board.accounts[0].last_comment_deleted is False
