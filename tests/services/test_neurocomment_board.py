"""Tests for ``services.neurocomment.board`` — the work-view read model.

Seeds real DB rows (campaign, channels, accounts, readiness, posted comments)
and asserts the assembled board: per-account quota usage and the per-channel
aggregate status derivation. Mirrors the warming board tests' seed-then-assert
approach.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, get_args

import pytest

from core.config import settings
from core.db import (
    assign_account_to_campaign,
    bump_channel_pause,
    claim_comment,
    configure_database,
    create_account,
    create_campaign,
    deactivate_channel,
    insert_challenge,
    link_channel_to_campaign,
    mark_comment_failed,
    mark_comment_posted,
    mark_comments_deleted,
    mark_human_skipped,
    mark_pair_banned,
    park_comment,
    record_comment_msg_id,
    save_neurocomment_settings,
    stamp_rejoin_attempt,
    upsert_linked_group,
    upsert_readiness,
)
from core.logging import reset_logging_for_tests, setup_logging
from core.repositories.neurocomment import set_campaign_account_channels
from schemas.accounts import AccountCreate
from schemas.challenge import ChallengeInsert
from schemas.neurocomment import CampaignCreate, NeurocommentSettingsUpdate
from services.neurocomment import _pair_status, _state
from services.neurocomment import board as board_module
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


async def _post_comment(
    channel: str,
    post_id: int,
    campaign_id: str,
    account_id: str,
    *,
    text: str = "hi",
) -> None:
    await claim_comment(channel, post_id, campaign_id, account_id)
    await mark_comment_posted(channel, post_id, comment_text=text, comment_msg_id=post_id)


@pytest.mark.asyncio
async def test_unknown_campaign_returns_none() -> None:
    assert await load_neurocomment_board("nope") is None


@pytest.mark.asyncio
async def test_channel_row_counts_recently_deleted_comments() -> None:
    campaign = await create_campaign(CampaignCreate(name="C1", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1", label="Account One"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    await _post_comment("@chan", 1, campaign.campaign_id, "acc-1")  # comment_msg_id == post_id
    await _post_comment("@chan", 2, campaign.campaign_id, "acc-1")
    await mark_comments_deleted("@chan", [1])  # one of the two removed

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].deleted_recent == 1


@pytest.mark.asyncio
async def test_card_deleted_today_outlives_the_channel_row() -> None:
    """The card's count survives unlinking the channel; the channel row's cannot.

    Why the board carries both: the header's "deleted" total sums the cards, so it stays
    a subset of the "comments" total. Summing the channel rows instead would drop to zero
    here while ``comments_today`` still reports 2 — and unlinking a channel that sweeps
    our comments is exactly what an operator does.
    """
    campaign = await create_campaign(CampaignCreate(name="C1", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await _post_comment("@chan", 1, campaign.campaign_id, "acc-1")
    await _post_comment("@chan", 2, campaign.campaign_id, "acc-1")
    await mark_comments_deleted("@chan", [1, 2])

    board = await load_neurocomment_board(campaign.campaign_id)
    assert board is not None
    assert board.accounts[0].deleted_today == 2

    await deactivate_channel(campaign.campaign_id, "@chan")

    board = await load_neurocomment_board(campaign.campaign_id)
    assert board is not None
    assert board.channels == []  # no row left to hang a per-channel count on
    assert board.accounts[0].comments_today == 2
    assert board.accounts[0].deleted_today == 2


@pytest.mark.asyncio
async def test_card_splits_its_deletions_by_channel() -> None:
    """The chip sits beside ONE channel name, so it counts that pair, not the account."""
    campaign = await create_campaign(CampaignCreate(name="C1", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    for post_id, channel in enumerate(("@news", "@old"), start=1):
        await link_channel_to_campaign(campaign.campaign_id, channel)
        await upsert_readiness("acc-1", channel, joined=True, captcha_passed=True, ready=True)
        await _post_comment(channel, post_id, campaign.campaign_id, "acc-1")
    await mark_comments_deleted("@old", [2])
    board = await load_neurocomment_board(campaign.campaign_id)
    assert board is not None
    assert board.accounts[0].deleted_today == 1  # the flat total is silent about WHICH one
    assert {r.channel: r.deleted for r in board.accounts[0].readiness} == {"@news": 0, "@old": 1}


@pytest.mark.asyncio
async def test_board_basic_shape() -> None:
    campaign = await create_campaign(CampaignCreate(name="C1", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1", label="Account One"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.campaign_name == "C1"
    assert board.status == "active"
    assert len(board.accounts) == 1
    card = board.accounts[0]
    assert card.account_id == "acc-1"
    assert card.label == "Account One"
    # No saved settings row → the effective cap falls back to live config.
    assert card.max_comments_per_hour == settings.neurocomment.max_comments_per_hour
    assert [r.channel for r in card.readiness] == ["@chan"]
    assert len(board.channels) == 1
    assert board.channels[0].channel == "@chan"
    assert board.channels[0].status == "ready"
    assert board.channels[0].ready_accounts == 1
    assert board.channels[0].total_accounts == 1


@pytest.mark.asyncio
async def test_card_carries_pinned_channels_and_empty_when_unpinned() -> None:
    """A pinned account's card reports its channel subset; an unpinned one reports []."""
    campaign = await create_campaign(CampaignCreate(name="C1", prompt="p"))
    await create_account(AccountCreate(account_id="pinned", label="Pinned"))
    await create_account(AccountCreate(account_id="free", label="Free"))
    await assign_account_to_campaign(campaign.campaign_id, "pinned")
    await assign_account_to_campaign(campaign.campaign_id, "free")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await set_campaign_account_channels(campaign.campaign_id, "pinned", ["@chan"])

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    pins = {card.account_id: card.pinned_channels for card in board.accounts}
    assert pins == {"pinned": ["@chan"], "free": []}


@pytest.mark.asyncio
async def test_card_counts_today_and_last_hour() -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    # Two posted comments, both within the day; both within the hour by default.
    await _post_comment("@chan", 1, campaign.campaign_id, "acc-1")
    await _post_comment("@chan", 2, campaign.campaign_id, "acc-1")

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    card = board.accounts[0]
    assert card.comments_today == 2
    assert card.comments_last_hour == 2
    assert card.last_comment_at is not None
    assert card.last_comment_text == "hi"


@pytest.mark.asyncio
async def test_card_hourly_count_includes_parked_posts() -> None:
    """The hourly number must count what the QUOTA counts, parked posts included.

    A parked post has already spent its slot (``_quota`` counts ``waiting``), so a card
    counting only ``posted`` showed free capacity for an account selection was refusing —
    the board contradicting the engine. ``comments_today`` keeps meaning "published".
    """
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await _post_comment("@chan", 1, campaign.campaign_id, "acc-1")
    assert await park_comment("@chan", 2, campaign.campaign_id, "acc-1") is True
    assert await park_comment("@chan", 3, campaign.campaign_id, "acc-1") is True

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    card = board.accounts[0]
    assert card.comments_last_hour == 3
    assert card.comments_today == 1
    assert card.deleted_today == 0


@pytest.mark.asyncio
async def test_card_hourly_count_ignores_another_campaigns_parked_post() -> None:
    # The parked-rows reader is fleet-wide, so a shared account's card would otherwise
    # carry a neighbouring campaign's wait — the same scoping the readiness chips get.
    await create_account(AccountCreate(account_id="acc-1"))
    this_campaign = await create_campaign(CampaignCreate(name="This", prompt="p"))
    other_campaign = await create_campaign(CampaignCreate(name="Other", prompt="p"))
    await assign_account_to_campaign(this_campaign.campaign_id, "acc-1")
    await assign_account_to_campaign(other_campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(this_campaign.campaign_id, "@mine")
    assert await park_comment("@theirs", 9, other_campaign.campaign_id, "acc-1") is True

    board = await load_neurocomment_board(this_campaign.campaign_id)

    assert board is not None
    assert board.accounts[0].comments_last_hour == 0


@pytest.mark.asyncio
async def test_card_names_the_channel_of_its_last_comment() -> None:
    """The channel travels on the card, beside the text of the SAME comment.

    The board's work row shows one channel per account and the text of its last comment.
    Deriving the channel from ``board.comments`` instead looks equivalent and is not: that
    feed is a campaign-wide newest-first prefix capped at ``board_comment_feed_limit``,
    while the card's ``last_comment_*`` come from the account's whole day window — so a
    busy account drops out of the feed and the row pairs its real comment with a channel it
    merely joined.
    """
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@first")
    await link_channel_to_campaign(campaign.campaign_id, "@second")
    await _post_comment("@first", 1, campaign.campaign_id, "acc-1")
    await _post_comment("@second", 2, campaign.campaign_id, "acc-1")

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    card = board.accounts[0]
    # Same row as the text: whichever comment is newest supplies both.
    latest = max(board.comments, key=lambda c: c.created_at)
    assert card.last_comment_channel == latest.channel
    assert card.last_comment_text == latest.comment_text


@pytest.mark.asyncio
async def test_board_comment_feed_is_recent_first() -> None:
    # The board carries a published-comments feed: every posted comment in the day
    # window, most-recent first (so the UI can show all N, not just the last one).
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await _post_comment("@chan", 1, campaign.campaign_id, "acc-1", text="first")
    await _post_comment("@chan", 2, campaign.campaign_id, "acc-1", text="second")

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert [c.comment_text for c in board.comments] == ["second", "first"]


@pytest.mark.asyncio
async def test_board_comment_feed_capped_to_config_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The feed is capped by config so a busy campaign can't unbound the payload;
    # the newest ones survive the cap.
    monkeypatch.setattr(settings.neurocomment, "board_comment_feed_limit", 2)
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    for post_id in (1, 2, 3):
        await _post_comment("@chan", post_id, campaign.campaign_id, "acc-1", text=str(post_id))

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert [c.comment_text for c in board.comments] == ["3", "2"]


@pytest.mark.asyncio
async def test_old_comment_excluded_from_day_window() -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await _post_comment("@chan", 1, campaign.campaign_id, "acc-1")
    # Backdate the row 2 days so it falls outside the day window.
    from core.db import _get_engine  # noqa: PLC0415 - test-only direct backdate.

    old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_comments SET created_at = ? WHERE post_id = 1",
            (old,),
        )

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.accounts[0].comments_today == 0


@pytest.mark.asyncio
async def test_channel_status_comments_off() -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_linked_group("@chan", None, comments_enabled=False)

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "comments_off"


@pytest.mark.asyncio
async def test_channel_status_chat_restricted() -> None:
    # Ф2 #120 state split + conservative remap: a joined-but-write-blocked row
    # (the pre-Ф2 captcha_gated boolean shape) now derives as ``chat_restricted``.
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=False, ready=False)

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "chat_restricted"


@pytest.mark.asyncio
async def test_channel_status_banned() -> None:
    # #30: a pair auto-banned while commenting surfaces as ``banned`` on the board,
    # taking precedence over the join-state fallbacks (and over chat_restricted).
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    await mark_pair_banned("acc-1", "@chan")

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "banned"


@pytest.mark.asyncio
async def test_channel_status_ready_wins_over_a_banned_sibling_account() -> None:
    # Two accounts serve @chan; one banned, one ready → the channel is still ready.
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await create_account(AccountCreate(account_id="acc-2"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await assign_account_to_campaign(campaign.campaign_id, "acc-2")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    await mark_pair_banned("acc-1", "@chan")
    await upsert_readiness("acc-2", "@chan", joined=True, captcha_passed=True, ready=True)

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "ready"


@pytest.mark.asyncio
async def test_channel_status_bot_challenge_when_challenge_row_exists() -> None:
    # Same joined-but-not-ready shape as chat_restricted, but a guardian-bot
    # challenge row was recorded → the board distinguishes it as bot_challenge.
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=False, ready=False)
    await insert_challenge(
        ChallengeInsert(
            challenge_hash="h1",
            account_id="acc-1",
            channel="@chan",
            raw_text="prove you are human",
            button_labels=["Я человек"],
            outcome="give_up",
        ),
    )

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "bot_challenge"


@pytest.mark.asyncio
async def test_channel_status_ignores_a_resolved_challenge_row() -> None:
    """A solved captcha leaves its old ``give_up`` behind — it must not name the wall.

    The badge picks between "a guardian bot is the wall" and ``chat_restricted`` on this one
    signal, and the table is append-only, so the stale row made a channel blocked by
    Telegram report a bot gate and sent the operator after the wrong fix. ``pass-er`` got
    through its captcha; ``blocked`` is joined and write-blocked with no challenge of its
    own, which is ``chat_restricted``.
    """
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    for account_id in ("passer", "blocked"):
        await create_account(AccountCreate(account_id=account_id))
        await assign_account_to_campaign(campaign.campaign_id, account_id)
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await insert_challenge(
        ChallengeInsert(
            challenge_hash="h1",
            account_id="passer",
            channel="@chan",
            raw_text="prove you are human",
            button_labels=["Я человек"],
            outcome="give_up",
        ),
    )
    # The later solve: in the chat and past the bot check, so nothing is captcha-blocked.
    await upsert_readiness("passer", "@chan", joined=True, captcha_passed=True, ready=False)
    await upsert_readiness("blocked", "@chan", joined=True, captcha_passed=False, ready=False)

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "chat_restricted"


@pytest.mark.asyncio
async def test_channel_status_channel_paused() -> None:
    # Ф2 #147: a channel serving out a "will not let us write" round shows channel_paused,
    # taking precedence over readiness. The board reads the deadline off the channel link
    # it already lists — no extra query per rendered row.
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await bump_channel_pause("@chan", (datetime.now(UTC) + timedelta(hours=24)).isoformat())

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "channel_paused"


@pytest.mark.asyncio
async def test_channel_status_join_by_request() -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_readiness("acc-1", "@chan", joined=False, captcha_passed=False, ready=False)

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "join_by_request"


@pytest.mark.asyncio
async def test_channel_status_rejoining_while_attempts_remain() -> None:
    # The join sentinel (captcha_passed on an unjoined row) is what a KICKED pair
    # carries while ``_rejoin`` walks it back into the chat, so it must not read as a
    # terminal "Не удалось вступить" — it self-resolves, and it is distinct from the
    # approval gate either way.
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_readiness("acc-1", "@chan", joined=False, captcha_passed=True, ready=False)

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "rejoining"


@pytest.mark.asyncio
async def test_channel_status_join_failed_once_the_rejoins_are_spent() -> None:
    # The same row with every re-join spent: nothing will retry it now, so this is the
    # one state the terminal join-failure badge is honest about.
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_readiness("acc-1", "@chan", joined=False, captcha_passed=True, ready=False)
    for _ in range(settings.neurocomment.channel_max_rounds):
        await stamp_rejoin_attempt("acc-1", "@chan")

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "join_failed"


@pytest.mark.asyncio
async def test_channel_status_join_failed_for_a_skipped_pair() -> None:
    # ``_rejoin`` refuses to re-join a pair the operator skipped (#148), so its sentinel
    # row never self-resolves either — terminal, not "getting back in".
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_readiness("acc-1", "@chan", joined=False, captcha_passed=True, ready=False)
    await mark_human_skipped("acc-1", "@chan")

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "join_failed"


@pytest.mark.asyncio
async def test_card_readiness_carries_the_banned_and_skipped_pairs() -> None:
    # A permanent per-pair ban (#30) is invisible on the channel row as soon as one
    # sibling account still posts there, so the card must carry it per (account,
    # channel) — that is the only place the operator can read WHICH account is burnt
    # WHERE. ``human_skipped`` (#148) rides the same row and was never populated.
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@burnt")
    await link_channel_to_campaign(campaign.campaign_id, "@skipped")
    await link_channel_to_campaign(campaign.campaign_id, "@fine")
    for channel in ("@burnt", "@skipped", "@fine"):
        await upsert_readiness("acc-1", channel, joined=True, captcha_passed=True, ready=True)
    await mark_pair_banned("acc-1", "@burnt")
    await mark_human_skipped("acc-1", "@skipped")

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    readiness = {r.channel: r for r in board.accounts[0].readiness}
    assert [r.channel for r in readiness.values() if r.banned] == ["@burnt"]
    assert [r.channel for r in readiness.values() if r.human_skipped] == ["@skipped"]
    assert not readiness["@fine"].banned


@pytest.mark.asyncio
async def test_channel_status_no_data_when_no_rows() -> None:
    # No readiness rows at all (onboarding hasn't produced data) and comments are
    # enabled → no_data, distinct from the throttled catch-all.
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_linked_group("@chan", 123, comments_enabled=True)

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "no_data"


@pytest.mark.asyncio
async def test_channel_status_throttled_when_joined_but_not_ready() -> None:
    # A joined, captcha-passed row that is not ready hits none of the gates → the
    # throttled catch-all (distinct from the no-rows no_data case).
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_linked_group("@chan", 123, comments_enabled=True)
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=False)

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "throttled"


def test_every_pair_verdict_the_shared_ladder_can_return_has_a_channel_badge() -> None:
    # ``_channel_status`` renders a pair verdict through two plain lookups that both fall
    # back to ``throttled``, so a rung added to ``_pair_status`` and to neither of them would
    # badge the wrong thing in silence: no type error, no missing translation, no failing
    # case above. This is what notices.
    rendered = board_module._AS_CHANNEL.keys() | set(board_module._CHANNEL_PRIORITY)

    assert set(get_args(_pair_status.PairBlock)) <= rendered


@pytest.mark.asyncio
async def test_card_readiness_scoped_to_this_campaigns_channels() -> None:
    # An account in two campaigns must show only THIS campaign's (account, channel)
    # readiness on its card — not the other campaign's channel chips.
    acc = "acc-1"
    await create_account(AccountCreate(account_id=acc))
    this_campaign = await create_campaign(CampaignCreate(name="This", prompt="p"))
    other_campaign = await create_campaign(CampaignCreate(name="Other", prompt="p"))
    await assign_account_to_campaign(this_campaign.campaign_id, acc)
    await assign_account_to_campaign(other_campaign.campaign_id, acc)
    await link_channel_to_campaign(this_campaign.campaign_id, "@mine")
    await link_channel_to_campaign(other_campaign.campaign_id, "@theirs")
    await upsert_readiness(acc, "@mine", joined=True, captcha_passed=True, ready=True)
    await upsert_readiness(acc, "@theirs", joined=True, captcha_passed=True, ready=True)

    board = await load_neurocomment_board(this_campaign.campaign_id)

    assert board is not None
    assert [r.channel for r in board.accounts[0].readiness] == ["@mine"]


@pytest.mark.asyncio
async def test_board_ignores_account_outside_campaign() -> None:
    # Scoped reads (#2): an existing account NOT assigned to the campaign must not
    # appear on the board.
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    for acc in ("acc-in", "acc-out"):
        await create_account(AccountCreate(account_id=acc))
    await assign_account_to_campaign(campaign.campaign_id, "acc-in")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert [card.account_id for card in board.accounts] == ["acc-in"]


@pytest.mark.asyncio
async def test_card_quota_denominator_is_the_saved_override() -> None:
    # The card must report the cap the engine enforces: the saved settings row (#19),
    # not the .env/config default — otherwise the UI shows "/10" against a real 20.
    await save_neurocomment_settings(
        NeurocommentSettingsUpdate(
            max_comments_per_hour=settings.neurocomment.max_comments_per_hour + 10,
            max_comments_per_channel_per_day=3,
            reply_delay_min_seconds=1,
            reply_delay_max_seconds=2,
            min_trust_score=40,
        ),
    )
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.accounts[0].max_comments_per_hour == (
        settings.neurocomment.max_comments_per_hour + 10
    )


@pytest.mark.asyncio
async def test_channel_row_counts_a_deleted_comment_recorded_as_failed() -> None:
    """What trips the back-off and what explains it must be the same set of comments.

    A comment whose claim was reclaimed mid-send reads ``failed`` while being live under the
    post. The sweep sees it — its scan set is "carries a message id" — and stamps it, so it
    drives the channel back-off. Counted off ``posted`` alone it contributed nothing, and the
    operator was shown a back-off with no deletions behind it.
    """
    campaign = await create_campaign(CampaignCreate(name="C1", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    assert await claim_comment("@chan", 1, campaign.campaign_id, "acc-1") is True
    await record_comment_msg_id("@chan", 1, 1)
    await mark_comment_failed("@chan", 1)
    await mark_comments_deleted("@chan", [1])

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].deleted_recent == 1
