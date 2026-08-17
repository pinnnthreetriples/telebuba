"""The sequential pass: ordering, durability and the reply chain.

Every test here drives ``engine.run_campaign`` against a real database and a fake
gateway, because the properties under test are exactly the ones a mocked repository
would assume away: that the journal row exists before the dispatch, that a row already
present stops a second send, and that a reply is aimed inside its own target.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select

from core.db import _get_engine
from core.repositories.neuroshilling import update_campaign
from core.repositories.neuroshilling._tables import _neuroshilling_messages
from schemas.neuroshilling import (
    NeuroshillingAccountAssignment,
    NeuroshillingCampaignUpdate,
)
from schemas.neuroshilling_scenario import NeuroshillingStepInput
from schemas.telegram_actions import CopyMessageMedia, PostComment, ResolveChatResult
from services.neuroshilling import _seams, _telegram, engine
from tests.services.neuroshilling.helpers import refused, seed_campaign, sent

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult, TelegramAction
    from tests.services.neuroshilling.helpers import Seeded

_RUN = "run-1"


class _Gateway:
    """A gateway that answers whatever the test queued and remembers the questions."""

    def __init__(self, answers: list[ActionResult] | None = None) -> None:
        self.answers = answers or []
        self.actions: list[tuple[str, TelegramAction]] = []
        self.rows_at_dispatch: list[int] = []

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        self.actions.append((account_id, action))
        self.rows_at_dispatch.append(await _count_rows())
        if self.answers:
            return self.answers.pop(0)
        return sent(100 + len(self.actions))


async def _count_rows() -> int:
    def _read() -> int:
        with _get_engine().connect() as connection:
            return len(connection.execute(select(_neuroshilling_messages.c.id)).all())

    import asyncio  # noqa: PLC0415 - one call, inside the only helper that needs it.

    return await asyncio.to_thread(_read)


async def _rows() -> list[dict[str, Any]]:
    def _read() -> list[dict[str, Any]]:
        with _get_engine().connect() as connection:
            statement = select(_neuroshilling_messages).order_by(_neuroshilling_messages.c.id)
            return [dict(row) for row in connection.execute(statement).mappings()]

    import asyncio  # noqa: PLC0415 - one call, inside the only helper that needs it.

    return await asyncio.to_thread(_read)


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> _Gateway:
    """Answer every write from the queue and every resolve with a usable megagroup."""
    fake = _Gateway()

    async def _resolve(_account_id: str, _action: TelegramAction) -> ResolveChatResult:
        return ResolveChatResult(chat_id=555, kind="megagroup")

    monkeypatch.setattr(_seams, "execute", fake.execute)
    monkeypatch.setattr(_seams, "execute_read", _resolve)
    monkeypatch.setattr(_telegram, "join_target", _joins)
    return fake


async def _joins(_campaign_id: str, _account_id: str, _target: str) -> str:
    return "joined"


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_sequential_run_posts_every_step_in_every_target(gateway: _Gateway) -> None:
    seeded = await seed_campaign(targets="@alpha @beta")

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert len(gateway.actions) == 4
    rows = await _rows()
    assert [row["status"] for row in rows] == ["sent"] * 4
    assert {row["target"] for row in rows} == {"alpha", "beta"}


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_the_row_is_written_before_the_send(gateway: _Gateway) -> None:
    """The window a send-then-write engine leaves is where double-posting lives.

    Counted at the moment of dispatch: the row for the step being sent must already
    exist, or a crash between the two leaves Telegram holding a message SQLite has
    never heard of and the next boot sends it again.
    """
    seeded = await seed_campaign()

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert gateway.rows_at_dispatch == [1, 2]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_step_that_already_has_a_row_is_not_sent_again(gateway: _Gateway) -> None:
    """The resume guarantee, exercised as two passes of the same run id."""
    seeded = await seed_campaign()
    await engine.run_campaign(seeded.campaign_id, _RUN)
    first = len(gateway.actions)

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert len(gateway.actions) == first
    assert len(await _rows()) == first


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_fresh_run_id_faces_an_empty_index(gateway: _Gateway) -> None:
    """Why a resumed run must NOT mint a new id — and what catches it if one does.

    The unique index is keyed on the run, so a new id claims every step again: two
    fresh rows, and nothing in the journal that says the dialogue has already been
    played here. What stops the words actually going out a second time is the content
    gate, which recognises the same text in the same chat — so the rows land as
    ``skipped`` and the gateway is never called.
    """
    seeded = await seed_campaign()
    await engine.run_campaign(seeded.campaign_id, _RUN)
    first = len(gateway.actions)

    await engine.run_campaign(seeded.campaign_id, "run-2")

    rows = await _rows()
    assert len(rows) == 2 * first
    assert [row["status"] for row in rows[first:]] == ["skipped"] * first
    assert len(gateway.actions) == first


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_an_unconfirmed_send_is_never_retried(gateway: _Gateway) -> None:
    """The request was already on the wire, so Telegram may hold the message.

    The row stays ``pending`` — which is what keeps its key occupied — and a second
    pass of the same run walks past it instead of publishing a duplicate.
    """
    gateway.answers = [refused("unavailable", error_type="UnconfirmedRequest")]
    seeded = await seed_campaign()

    await engine.run_campaign(seeded.campaign_id, _RUN)
    first = [row["status"] for row in await _rows()]
    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert first[0] == "pending"
    assert len(gateway.actions) == 2
    assert [row["status"] for row in await _rows()] == first


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_reply_is_aimed_inside_its_own_target(gateway: _Gateway) -> None:
    """A lookup by step alone would hand target two the id from target one."""
    seeded = await seed_campaign(targets="@alpha @beta")

    await engine.run_campaign(seeded.campaign_id, _RUN)

    replies = [action for _account, action in gateway.actions if isinstance(action, PostComment)]
    # Steps 1 and 3 open their target; 2 and 4 answer the one just before them.
    assert [action.reply_to for action in replies] == [None, 101, None, 103]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_missing_anchor_climbs_the_reply_chain(gateway: _Gateway) -> None:
    """Step 3 answers step 2, which failed; the reply lands on step 1 instead.

    Aiming at nothing is the alternative, and a staged dialogue whose thread is
    silently broken reads worse to a human than one message short.
    """
    steps = [
        NeuroshillingStepInput(role_id="#0", text="one", delay_min_seconds=0, delay_max_seconds=0),
        NeuroshillingStepInput(
            role_id="#1",
            text="two",
            reply_to_position=1,
            delay_min_seconds=0,
            delay_max_seconds=0,
        ),
        NeuroshillingStepInput(
            role_id="#0",
            text="three",
            reply_to_position=2,
            delay_min_seconds=0,
            delay_max_seconds=0,
        ),
    ]
    gateway.answers = [sent(101), refused("failed", error_type="RpcError"), sent(103)]
    seeded = await seed_campaign(steps=steps)

    await engine.run_campaign(seeded.campaign_id, _RUN)

    third = gateway.actions[2][1]
    assert isinstance(third, PostComment)
    assert third.reply_to == 101


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_target_nobody_could_join_is_skipped_without_sending(
    gateway: _Gateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _refused(_campaign_id: str, _account_id: str, _target: str) -> str:
        return "refused"

    monkeypatch.setattr(_telegram, "join_target", _refused)
    seeded = await seed_campaign()

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert gateway.actions == []
    assert await _rows() == []


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_failing_account_is_not_dealt_the_next_step_as_well(
    gateway: _Gateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal is load. Counting only what was PUBLISHED left the loser winning.

    ``not_member`` reaches the engine as a bare ``ValueError`` and settles the row
    ``failed`` — a status the selection score did not count, so the account stayed at
    zero, remained the minimum for every later step, and the whole dialogue failed in
    that chat while its working sibling was never asked.
    """
    # Ties break through the rng seam; pinned to the first candidate so the account
    # that fails is the one the roster lists first.
    monkeypatch.setattr(_seams.rng, "choice", lambda seq: seq[0])
    gateway.answers = [refused("failed", error_type="ValueError")]
    seeded = await seed_campaign(accounts=("acc-1", "acc-2"), steps=_one_role_steps(2))
    await _put_both_accounts_on_one_role(seeded)

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert [account_id for account_id, _action in gateway.actions] == ["acc-1", "acc-2"]
    assert [row["status"] for row in await _rows()] == ["failed", "sent"]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_cast_with_nobody_in_it_says_so_instead_of_finishing_quietly(
    gateway: _Gateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``all()`` over an empty cast is True, which returned before the first target.

    The launch gate refuses a role with no account, but a RESUMED run never passes
    through it — so a roster edited between two runs reached here and settled ``done``
    having sent nothing and said nothing about why.
    """
    events: list[str] = []

    async def _capture(_level: str, event: str, **_fields: object) -> None:
        events.append(event)

    monkeypatch.setattr(engine, "log_event", _capture)
    seeded = await seed_campaign(accounts=("acc-1",))
    await _take_every_role_off_the_roster(seeded)

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert events == ["neuroshilling_no_speakers", "neuroshilling_target_failed"]
    assert gateway.actions == []


async def _take_every_role_off_the_roster(seeded: Seeded) -> None:
    """The roster edit a run cannot see coming: same accounts, no parts to play."""
    await update_campaign(
        seeded.campaign_id,
        NeuroshillingCampaignUpdate(
            name="Promo",
            targets_raw="@alpha",
            accounts=[
                NeuroshillingAccountAssignment(account_id=account_id)
                for account_id in seeded.accounts
            ],
        ),
    )


def _one_role_steps(count: int) -> list[NeuroshillingStepInput]:
    return [
        NeuroshillingStepInput(
            role_id="#0",
            text=f"line {index}",
            delay_min_seconds=0,
            delay_max_seconds=0,
        )
        for index in range(count)
    ]


async def _put_both_accounts_on_one_role(seeded: Seeded) -> None:
    """Two accounts able to play the same part — the case a substitution needs."""
    await update_campaign(
        seeded.campaign_id,
        NeuroshillingCampaignUpdate(
            name="Promo",
            targets_raw="@alpha",
            accounts=[
                NeuroshillingAccountAssignment(
                    account_id=account_id, role_id=seeded.roles[0].role_id
                )
                for account_id in seeded.accounts
            ],
        ),
    )


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_failed_media_step_gives_its_dedup_reservation_back(gateway: _Gateway) -> None:
    """A captionless media step reserves the bare target's hash and must release it.

    The release used to be guarded on the text being non-empty, so nothing published
    still held that hash for the whole 7-day dedup window — blocking not just a retry
    but every other captionless media step into the same chat.
    """
    gateway.answers = [refused("failed", error_type="RpcError")]
    seeded = await seed_campaign(
        accounts=("acc-1",),
        steps=[
            NeuroshillingStepInput(
                role_id="#0",
                text="",
                delay_min_seconds=0,
                delay_max_seconds=0,
            ),
        ],
        media_message_link="https://t.me/chan/7",
        media_step_position=1,
    )

    await engine.run_campaign(seeded.campaign_id, _RUN)
    await engine.run_campaign(seeded.campaign_id, "run-2")

    assert isinstance(gateway.actions[0][1], CopyMessageMedia)
    assert [row["status"] for row in await _rows()] == ["failed", "sent"]
