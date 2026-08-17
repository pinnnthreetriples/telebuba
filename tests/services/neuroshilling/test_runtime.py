"""Start, Stop, boot reconciliation and shutdown — the run's lifecycle.

The two properties this file exists for: a Stop must reach a coroutine that is asleep
inside a step delay (a status flip cannot), and a resumed run must carry the STORED
``run_id`` forward, because a fresh one faces an empty unique index and replays the
whole dialogue into chats that already have it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from sqlalchemy import update as sql_update

from core.config import settings
from core.db import _get_engine
from core.repositories import neuroshilling as repository
from core.repositories.neuroshilling._tables import (
    _neuroshilling_campaigns,
    _neuroshilling_messages,
)
from schemas.neuroshilling import (
    NeuroshillingAccountAssignment,
    NeuroshillingCampaignUpdate,
    NeuroshillingStepKey,
)
from schemas.telegram_actions import ResolveChatResult
from services import _account_owner
from services.neuroshilling import _runtime, _seams, _state, _telegram, engine
from services.neuroshilling.campaigns import NeuroshillingConflictError
from tests.services.neuroshilling.helpers import seed_campaign, sent

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult, TelegramAction


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> list[TelegramAction]:
    seen: list[TelegramAction] = []

    async def _execute(_account_id: str, action: TelegramAction) -> ActionResult:
        seen.append(action)
        return sent(100 + len(seen))

    async def _resolve(_account_id: str, _action: TelegramAction) -> ResolveChatResult:
        return ResolveChatResult(chat_id=555, kind="megagroup")

    async def _joins(_campaign_id: str, _account_id: str, _target: str) -> str:
        return "joined"

    monkeypatch.setattr(_seams, "execute", _execute)
    monkeypatch.setattr(_seams, "execute_read", _resolve)
    monkeypatch.setattr(_telegram, "join_target", _joins)
    return seen


async def _drain() -> None:
    """Let every spawned run task finish before the test asserts on the rows."""
    tasks = set(_runtime._TASKS.values())
    if not tasks:
        return
    _done, pending = await asyncio.wait(tasks, timeout=10)
    assert not pending, "a run task never finished"


async def _journal_row(key: NeuroshillingStepKey) -> tuple[str, str | None] | None:
    """``(status, error_type)`` for one journal row — the columns no reader exposes."""

    def _read() -> tuple[str, str | None] | None:
        statement = select(
            _neuroshilling_messages.c.status,
            _neuroshilling_messages.c.error_type,
        ).where(
            (_neuroshilling_messages.c.run_id == key.run_id)
            & (_neuroshilling_messages.c.target == key.target)
            & (_neuroshilling_messages.c.step_id == key.step_id),
        )
        with _get_engine().connect() as connection:
            row = connection.execute(statement).first()
        return None if row is None else (str(row[0]), row[1])

    return await asyncio.to_thread(_read)


@pytest.mark.usefixtures("no_sleep", "gateway")
@pytest.mark.asyncio
async def test_start_runs_the_campaign_and_gives_the_accounts_back() -> None:
    seeded = await seed_campaign()

    status = await _runtime.start_campaign(seeded.campaign_id)
    assert status is not None
    assert status.status == "running"
    await _drain()

    campaign = await repository.fetch_campaign(seeded.campaign_id)
    assert campaign is not None
    assert (campaign.status, campaign.run_id) == ("done", None)
    assert _account_owner.owner_of("acc-1") is None


@pytest.mark.asyncio
async def test_a_running_campaign_holds_every_account_of_its_roster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asserted against a run that is still going: a finished one has already let go."""
    running = asyncio.Event()

    async def _never_ends(_campaign_id: str, _run_id: str) -> None:
        await running.wait()

    monkeypatch.setattr(engine, "run_campaign", _never_ends)
    seeded = await seed_campaign()

    await _runtime.start_campaign(seeded.campaign_id)

    assert _account_owner.owner_of("acc-1") == "neuroshilling"
    assert _account_owner.holder_of("acc-2") == seeded.campaign_id
    await _runtime.stop_campaign(seeded.campaign_id)


@pytest.mark.asyncio
async def test_start_refuses_a_draft_scenario() -> None:
    seeded = await seed_campaign(approve=False)

    with pytest.raises(NeuroshillingConflictError) as refusal:
        await _runtime.start_campaign(seeded.campaign_id)

    assert refusal.value.code == "scenario_not_approved"
    assert _account_owner.owner_of("acc-1") is None


@pytest.mark.asyncio
async def test_start_refuses_parallel_run_mode() -> None:
    """Refused on the SERVER: the generated client types the field, so hiding it is not enough."""
    seeded = await seed_campaign()
    await repository.set_run_state(seeded.campaign_id, "idle", run_id=None)

    def _write() -> None:
        with _get_engine().begin() as connection:
            connection.execute(
                sql_update(_neuroshilling_campaigns)
                .where(_neuroshilling_campaigns.c.campaign_id == seeded.campaign_id)
                .values(run_mode="parallel"),
            )

    # Written straight to the column because the update endpoint refuses the value too,
    # which is exactly what makes this the second gate rather than the same one twice.
    await asyncio.to_thread(_write)

    with pytest.raises(NeuroshillingConflictError) as refusal:
        await _runtime.start_campaign(seeded.campaign_id)

    assert refusal.value.code == "run_mode_not_supported"


@pytest.mark.asyncio
async def test_start_refuses_a_campaign_with_no_targets() -> None:
    seeded = await seed_campaign(targets="")

    with pytest.raises(NeuroshillingConflictError) as refusal:
        await _runtime.start_campaign(seeded.campaign_id)

    assert refusal.value.code == "no_targets"


@pytest.mark.asyncio
async def test_start_refuses_a_role_with_no_account() -> None:
    """A dialogue with a silent part in it is not a dialogue anybody staged."""
    seeded = await seed_campaign(accounts=("acc-1", "acc-2", "acc-3"))
    # Drop the last account from the roster, leaving its role in the dialogue unstaffed.
    # Roster edits do not reset the approval, so the campaign is still launchable.
    await repository.update_campaign(
        seeded.campaign_id,
        NeuroshillingCampaignUpdate(
            name="Promo",
            targets_raw="@alpha",
            accounts=[
                NeuroshillingAccountAssignment(account_id=account, role_id=role.role_id)
                for account, role in zip(seeded.accounts[:2], seeded.roles, strict=False)
            ],
        ),
    )

    with pytest.raises(NeuroshillingConflictError) as refusal:
        await _runtime.start_campaign(seeded.campaign_id)

    assert refusal.value.code == "role_without_account"


@pytest.mark.asyncio
async def test_a_second_campaign_cannot_start_on_a_held_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An account may be ASSIGNED to many campaigns, but held by one RUNNING one.

    The first run is held open, because a finished one has already given the account
    back and the second start would rightly succeed.
    """
    running = asyncio.Event()

    async def _never_ends(_campaign_id: str, _run_id: str) -> None:
        await running.wait()

    monkeypatch.setattr(engine, "run_campaign", _never_ends)
    first = await seed_campaign()
    second = await seed_campaign(accounts=("acc-1", "acc-2"), targets="@beta")
    await _runtime.start_campaign(first.campaign_id)

    with pytest.raises(NeuroshillingConflictError) as refusal:
        await _runtime.start_campaign(second.campaign_id)

    assert refusal.value.code == "account_busy"
    assert _account_owner.holder_of("acc-1") == first.campaign_id
    await _runtime.stop_campaign(first.campaign_id)


@pytest.mark.asyncio
async def test_a_refused_start_takes_no_account_with_it() -> None:
    """The claim loop is all-or-nothing: a refusal halfway gives back what it took."""
    seeded = await seed_campaign(accounts=("acc-1", "acc-2", "acc-3"))
    _account_owner.try_claim("acc-3", "warming", "run-w")

    with pytest.raises(NeuroshillingConflictError):
        await _runtime.start_campaign(seeded.campaign_id)

    assert _account_owner.owner_of("acc-1") is None
    assert _account_owner.owner_of("acc-2") is None


@pytest.mark.asyncio
async def test_stop_prevents_a_sleeping_step_from_posting(
    monkeypatch: pytest.MonkeyPatch,
    gateway: list[TelegramAction],
) -> None:
    """A ``status='stopping'`` row cannot do this: nothing reads a row leaving a sleep."""
    parked = asyncio.Event()
    forever = asyncio.Event()

    async def _sleep(_seconds: float) -> None:
        parked.set()
        await forever.wait()

    monkeypatch.setattr(_seams, "sleep", _sleep)
    monkeypatch.setattr("services.neuroshilling._seams.await_send_slot", _noop_slot)
    seeded = await seed_campaign()
    await _runtime.start_campaign(seeded.campaign_id)
    await asyncio.wait_for(parked.wait(), timeout=1)

    status = await _runtime.stop_campaign(seeded.campaign_id)

    assert status is not None
    assert status.status == "done"
    assert gateway == []
    assert _account_owner.owner_of("acc-1") is None


async def _noop_slot(*_args: object) -> None:
    return None


@pytest.mark.usefixtures("no_sleep", "gateway")
@pytest.mark.asyncio
async def test_stopping_an_idle_campaign_is_a_no_op() -> None:
    seeded = await seed_campaign()

    status = await _runtime.stop_campaign(seeded.campaign_id)

    assert status is not None
    assert status.status == "idle"


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_resume_reuses_the_persisted_run_id(gateway: list[TelegramAction]) -> None:
    """A fresh id would face an empty index and replay every step already delivered.

    Asserted on the journal rows the resumed pass WROTE, and not on the silence at the
    gateway: the content gate refuses the same words in the same chat whatever id they
    are claimed under, so a run that minted a fresh id would send nothing either and
    pass a test that only counted sends.
    """
    seeded = await seed_campaign()
    await _runtime.start_campaign(seeded.campaign_id)
    await _drain()
    # Put the campaign back the way a killed process leaves it.
    await repository.set_run_state(seeded.campaign_id, "running", run_id="run-kept")
    played = len(gateway)

    await _runtime.reconcile_neuroshilling_on_startup()
    await _drain()

    campaign = await repository.fetch_campaign(seeded.campaign_id)
    assert campaign is not None
    assert campaign.status == "done"
    assert await repository.list_journalled_steps("run-kept") == {
        ("alpha", step.step_id) for step in seeded.steps
    }
    # Nothing new went out: the dialogue's own rows are gone with the old run id, but
    # the content gate recognises the same words in the same chat.
    assert len(gateway) == played


@pytest.mark.usefixtures("no_sleep", "gateway")
@pytest.mark.asyncio
async def test_reconcile_settles_the_rows_a_killed_process_left_pending() -> None:
    """The row keeps its key and stops being ``pending``, which is what settling means.

    Read as a STATUS, because that is the only thing the sweep changes: the row was
    already journalled and already had no message id before it ran, so both of those
    answers are the same whether or not it was called at all.
    """
    seeded = await seed_campaign()
    await repository.set_run_state(seeded.campaign_id, "running", run_id="run-old")
    key = NeuroshillingStepKey(
        run_id="run-old",
        target="alpha",
        step_id=seeded.steps[0].step_id,
    )
    await repository.claim_message(
        key,
        campaign_id=seeded.campaign_id,
        account_id="acc-1",
        text="line 0",
    )
    assert await _journal_row(key) == ("pending", None)

    await _runtime.reconcile_neuroshilling_on_startup()
    await _drain()

    assert await _journal_row(key) == ("failed", "InterruptedRun")


@pytest.mark.usefixtures("no_sleep", "gateway")
@pytest.mark.asyncio
async def test_reconcile_fails_a_campaign_whose_account_another_feature_took() -> None:
    """Half a resumed campaign is worse than none: the dialogue would be missing a voice."""
    seeded = await seed_campaign()
    await repository.set_run_state(seeded.campaign_id, "running", run_id="run-old")
    _account_owner.try_claim("acc-2", "warming", "run-w")

    await _runtime.reconcile_neuroshilling_on_startup()

    campaign = await repository.fetch_campaign(seeded.campaign_id)
    assert campaign is not None
    assert (campaign.status, campaign.last_error) == ("failed", "AccountBusy")


@pytest.mark.usefixtures("no_sleep", "gateway")
@pytest.mark.asyncio
async def test_reconcile_finishes_a_stop_rather_than_undoing_it() -> None:
    """A restart must not put the fleet back into chats a human switched it out of.

    ``stopping`` is a LIVE status, so the row reaches reconciliation with everything
    else, and resuming on ``run_id is not None`` alone re-claimed it, flipped it back
    to ``running`` and span a task up. The row exists for the whole of Stop's drain and
    for good if the process died at the flip — a SIGTERM in a deploy is enough.
    """
    seeded = await seed_campaign()
    await repository.set_run_state(seeded.campaign_id, "stopping", run_id="run-old")

    await _runtime.reconcile_neuroshilling_on_startup()

    campaign = await repository.fetch_campaign(seeded.campaign_id)
    assert campaign is not None
    assert (campaign.status, campaign.run_id) == ("done", None)
    assert _runtime._TASKS == {}
    assert [_account_owner.owner_of(account_id) for account_id in seeded.accounts] == [None, None]


@pytest.mark.asyncio
async def test_a_late_finisher_never_writes_over_a_newer_run() -> None:
    """The fence that stops run N settling on top of run N+1's ``running`` row."""
    seeded = await seed_campaign()
    _state.begin_run(seeded.campaign_id, "run-old")
    _state.begin_run(seeded.campaign_id, "run-new")
    await repository.set_run_state(seeded.campaign_id, "running", run_id="run-new")

    await _runtime._settle(seeded.campaign_id, "run-old", "done", None)

    campaign = await repository.fetch_campaign(seeded.campaign_id)
    assert campaign is not None
    assert (campaign.status, campaign.run_id) == ("running", "run-new")


@pytest.mark.asyncio
async def test_shutdown_leaves_the_campaign_running_for_the_next_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``running`` row IS the signal reconciliation reads; settling it would lose it."""
    forever = asyncio.Event()

    async def _never_ends(_campaign_id: str, _run_id: str) -> None:
        await forever.wait()

    monkeypatch.setattr(engine, "run_campaign", _never_ends)
    seeded = await seed_campaign()
    await _runtime.start_campaign(seeded.campaign_id)

    await _runtime.shutdown_neuroshilling_on_shutdown()

    campaign = await repository.fetch_campaign(seeded.campaign_id)
    assert campaign is not None
    assert campaign.status == "running"
    assert campaign.run_id is not None
    assert _account_owner.owner_of("acc-1") is None


@pytest.mark.usefixtures("no_sleep", "gateway")
@pytest.mark.asyncio
async def test_two_starts_at_once_leave_exactly_one_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The status column cannot carry "one running campaign" on its own.

    ``running`` is written three awaits after the check that reads it, and
    ``_account_owner.try_claim`` is idempotent for the same (owner, holder) pair — so
    it refuses the second start no more than the status does. Two run tasks resulted,
    with ``_TASKS`` holding only the second and the first unreachable to Stop.
    """
    running = asyncio.Event()
    spawned = 0

    async def _never_ends(_campaign_id: str, _run_id: str) -> None:
        nonlocal spawned
        spawned += 1
        await running.wait()

    monkeypatch.setattr(engine, "run_campaign", _never_ends)
    seeded = await seed_campaign()

    outcomes = await asyncio.gather(
        _runtime.start_campaign(seeded.campaign_id),
        _runtime.start_campaign(seeded.campaign_id),
        return_exceptions=True,
    )

    refusals = [item for item in outcomes if isinstance(item, NeuroshillingConflictError)]
    assert [item.code for item in refusals] == ["campaign_running"]
    # Counted inside the run itself: ``_TASKS`` holds one entry either way, because the
    # second spawn overwrites the first rather than being refused.
    assert spawned == 1
    await _runtime.stop_campaign(seeded.campaign_id)


@pytest.mark.usefixtures("no_sleep", "gateway")
@pytest.mark.asyncio
async def test_a_run_that_settles_mid_stop_does_not_wedge_the_campaign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop reads a live campaign, the run finishes, and only then does Stop write.

    That write resurrects a terminal row as ``stopping``. The run task had already
    taken the settlement entry away, so Stop's own fallback settle used to be refused
    and the campaign answered ``campaign_running`` to every Start until a restart.
    """
    finish = asyncio.Event()

    async def _waits(_campaign_id: str, _run_id: str) -> None:
        await finish.wait()

    monkeypatch.setattr(engine, "run_campaign", _waits)
    seeded = await seed_campaign()
    await _runtime.start_campaign(seeded.campaign_id)
    real_set_run_state = repository.set_run_state

    async def _let_the_run_settle_first(
        campaign_id: str,
        status: str,
        *,
        run_id: str | None,
        last_error: str | None = None,
    ) -> None:
        if status == "stopping":
            finish.set()
            await _drain()
        await real_set_run_state(campaign_id, status, run_id=run_id, last_error=last_error)

    monkeypatch.setattr(repository, "set_run_state", _let_the_run_settle_first)

    await _runtime.stop_campaign(seeded.campaign_id)

    campaign = await repository.fetch_campaign(seeded.campaign_id)
    assert campaign is not None
    assert (campaign.status, campaign.run_id) == ("done", None)
    assert _account_owner.owner_of("acc-1") is None


@pytest.mark.usefixtures("no_sleep", "gateway")
@pytest.mark.asyncio
async def test_stopping_inside_a_step_gives_the_reserved_slot_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row reserved before a dispatch nobody made must not spend a slot for ever.

    The journal row goes in BEFORE the send, and ``pending`` counts against the caps —
    that is what makes an in-flight send visible. A Stop landing between the two leaves
    the row behind and then clears ``run_id``, so no later boot sweep can even find it,
    while the per-campaign total for that account has no window to forget it: ten such
    stops are ten messages the account may never send again.
    """
    dispatching = asyncio.Event()

    async def _parks(_account_id: str, _action: TelegramAction) -> ActionResult:
        dispatching.set()
        await asyncio.Event().wait()
        raise AssertionError  # unreachable: the wait above only ends in cancellation

    monkeypatch.setattr(_seams, "execute", _parks)
    seeded = await seed_campaign()
    await _runtime.start_campaign(seeded.campaign_id)
    await asyncio.wait_for(dispatching.wait(), timeout=1)
    live = await repository.fetch_campaign(seeded.campaign_id)
    assert live is not None
    assert live.run_id is not None

    await _runtime.stop_campaign(seeded.campaign_id)

    # The row stays, because its key must go on being occupied — nothing may replay a
    # step whose dispatch might have reached Telegram after all.
    assert ("alpha", seeded.steps[0].step_id) in await repository.list_journalled_steps(
        live.run_id,
    )
    # And it is settled, so the slot it reserved is back in the account's allowance.
    usage = await repository.read_quota_usage(
        seeded.campaign_id,
        "acc-1",
        "alpha",
        hour_since="2000-01-01T00:00:00+00:00",
        day_since="2000-01-01T00:00:00+00:00",
    )
    assert usage.campaign_total == 0


@pytest.mark.usefixtures("no_sleep", "gateway")
@pytest.mark.asyncio
async def test_a_stop_overtaken_by_a_start_leaves_the_new_run_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop acts on a row a thread hop old; a whole new run can fit in that hop.

    Driven by hand at the one point that produces it — the row is read, and before Stop
    can act on it the run settles and the operator starts another. A ``gather`` cannot
    arrange that: it would have to land the Start inside a single ``to_thread`` of Stop.

    Unconditionally, Stop then fenced the NEW run, wrote its own ``stopping`` over the new
    run id and cancelled the new task — and its settle was refused, because the successor
    owned the settlement. The campaign stayed ``stopping`` with nothing playing it: Start
    answered ``campaign_running``, another Stop took the same path to the same refusal,
    and only a restart cleared it.
    """
    first_finished = asyncio.Event()
    runs = 0

    async def _first_ends_on_demand(_campaign_id: str, _run_id: str) -> None:
        nonlocal runs
        runs += 1
        if runs == 1:
            await first_finished.wait()
            return
        await asyncio.Event().wait()

    monkeypatch.setattr(engine, "run_campaign", _first_ends_on_demand)
    seeded = await seed_campaign()
    await _runtime.start_campaign(seeded.campaign_id)
    stopped = await repository.fetch_campaign(seeded.campaign_id)
    assert stopped is not None
    real_fetch = repository.fetch_campaign
    overtake = True

    async def _let_a_whole_run_turn_over(campaign_id: str) -> object:
        nonlocal overtake
        row = await real_fetch(campaign_id)
        if overtake:
            # Everything below happens while Stop is still holding this row: the run it
            # names settles, and the operator's next Start mints a successor.
            overtake = False
            first_finished.set()
            await _drain()
            await _runtime.start_campaign(campaign_id)
        return row

    monkeypatch.setattr(repository, "fetch_campaign", _let_a_whole_run_turn_over)

    await _runtime.stop_campaign(seeded.campaign_id)

    monkeypatch.setattr(repository, "fetch_campaign", real_fetch)
    successor = await repository.fetch_campaign(seeded.campaign_id)
    assert successor is not None
    assert successor.status == "running"
    assert successor.run_id not in (None, stopped.run_id)
    task = _runtime._TASKS.get(seeded.campaign_id)
    assert task is not None
    assert not task.done()
    # And the operator is not locked out: a Stop of the run that IS live still works.
    ended = await _runtime.stop_campaign(seeded.campaign_id)
    assert ended is not None
    assert ended.status == "done"


@pytest.mark.usefixtures("no_sleep", "gateway")
@pytest.mark.asyncio
async def test_stop_keeps_the_roster_until_a_slow_run_has_unwound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired drain means a dispatch may still be on the wire on that session.

    Releasing then would let warming — or another campaign — claim an account whose
    old run is still inside a call. The terminal row is written either way, because
    the fence has already stopped the run publishing anything more.
    """
    unwinding = asyncio.Event()

    async def _slow_to_unwind(_campaign_id: str, _run_id: str) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await unwinding.wait()
            raise

    monkeypatch.setattr(engine, "run_campaign", _slow_to_unwind)
    monkeypatch.setattr(settings.neuroshilling, "stop_drain_seconds", 0.01)
    seeded = await seed_campaign()
    await _runtime.start_campaign(seeded.campaign_id)

    status = await _runtime.stop_campaign(seeded.campaign_id)

    assert status is not None
    assert status.status == "done"
    assert _account_owner.owner_of("acc-1") == "neuroshilling"
    unwinding.set()
    await _drain()
    await asyncio.sleep(0)
    assert _account_owner.owner_of("acc-1") is None
