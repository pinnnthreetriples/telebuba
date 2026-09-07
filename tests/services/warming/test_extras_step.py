"""Extras step — sampler, eligibility, budget tiers, flood halt, rail emission, order."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, cast

import pytest

from core.config import settings
from core.db import save_warming_settings
from schemas._warming_extras import EXTRA_TOGGLE_DEFAULTS
from schemas.telegram_actions import ActionResult
from schemas.telegram_actions_warming import (
    WarmCheckSettings,
    WarmGetDialogs,
    WarmReadContacts,
    WarmViewProfile,
)
from schemas.warming import WarmingChannel, WarmingCycleRequest, WarmingSettingsSecret
from services import warming
from services.warming import _extras, _extras_reads, _seams
from services.warming._extras import (
    EXTRAS,
    _fold_extra,
    _is_eligible,
    _pick_extras,
    run_extras_step,
)
from services.warming._extras_ctx import _ExtraContext, _ExtraSpec, _write
from services.warming._steps import _ChannelTally
from tests.services.warming._support import _account, _Recorder, _seed_ready_account, _set_settings

if TYPE_CHECKING:
    from schemas._warming_extras import ExtraToggles
    from schemas.telegram_actions import TelegramAction
    from services.warming._extras_ctx import _Need

_ALL_ON = cast("ExtraToggles", dict.fromkeys(EXTRA_TOGGLE_DEFAULTS, True))
_KEYS = [spec.key for spec in EXTRAS]
_CHANNEL = WarmingChannel(channel="c1", created_at="2026-01-01T00:00:00+00:00")


def _secret() -> WarmingSettingsSecret:
    return WarmingSettingsSecret(
        inter_account_chat=False,
        reactions_enabled=False,
        gemini_api_key="",
        gemini_model="m",
        updated_at="now",
        extra_toggles=_ALL_ON,
    )


def _ctx(  # noqa: PLR0913 - one keyword per context fact reads clearer in the tests.
    *,
    chosen: list[WarmingChannel] | None = None,
    recent_ids: dict[str, list[int]] | None = None,
    remaining: int | None = None,
    tally: _ChannelTally | None = None,
    premium: bool | None = None,
    no_account: bool = False,
) -> _ExtraContext:
    return _ExtraContext(
        account_id="acc-1",
        account=None if no_account else _account(premium=premium),
        secret=_secret(),
        persona="normal",
        chosen=[] if chosen is None else chosen,
        recent_ids={} if recent_ids is None else recent_ids,
        tally=tally or _ChannelTally(),
        remaining_actions=remaining,
    )


def _extras_range(monkeypatch: pytest.MonkeyPatch, lo: int, hi: int) -> None:
    monkeypatch.setattr(settings.warming, "persona_extras", {"normal": (lo, hi)})


class _StatusAt:
    """Dispatcher returning ``status`` on the N-th call (1-based), ``ok`` otherwise."""

    def __init__(self, nth: int, status: str) -> None:
        self.actions: list[TelegramAction] = []
        self._nth = nth
        self._status = status

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        self.actions.append(action)
        status = self._status if len(self.actions) == self._nth else "ok"
        return ActionResult.model_validate(
            {
                "status": status,
                "action_type": action.action_type,
                "account_id": account_id,
                "flood_wait_seconds": 60 if status == "flood_wait" else None,
            },
        )


# --- sampler -----------------------------------------------------------------


def test_pick_extras_is_capped_by_the_eligible_count(monkeypatch: pytest.MonkeyPatch) -> None:
    _extras_range(monkeypatch, 9, 9)
    toggles = {"dialogs": True, "contacts": True}
    picked = {s.key for s in _pick_extras(_ctx(), toggles, random.Random(7))}  # noqa: S311
    assert picked == {"dialogs", "contacts"}


def test_pick_extras_draws_nothing_for_a_zero_range(monkeypatch: pytest.MonkeyPatch) -> None:
    _extras_range(monkeypatch, 0, 0)
    assert _pick_extras(_ctx(), _ALL_ON, random.Random(7)) == []  # noqa: S311


def test_toggled_off_or_missing_key_is_never_picked(monkeypatch: pytest.MonkeyPatch) -> None:
    _extras_range(monkeypatch, 9, 9)
    rng = random.Random(7)  # noqa: S311
    picked = {s.key for s in _pick_extras(_ctx(), {**_ALL_ON, "dialogs": False}, rng)}
    assert picked == set(_KEYS) - {"dialogs"}
    # A key absent from the mapping is off, not on.
    assert _pick_extras(_ctx(), {}, random.Random(7)) == []  # noqa: S311


# --- eligibility -------------------------------------------------------------


def _spec(*needs: _Need) -> _ExtraSpec:
    return _ExtraSpec("synthetic", "read", _extras_reads.contacts, frozenset(needs))


@pytest.mark.parametrize(
    ("needs", "ctx", "expected"),
    [
        ((), _ctx(), True),
        (("recent_ids",), _ctx(), False),
        (("recent_ids",), _ctx(recent_ids={"c1": []}), False),
        (("recent_ids",), _ctx(recent_ids={"c1": [7]}), True),
        (("joined",), _ctx(), False),
        (("premium",), _ctx(premium=True), True),
        (("premium",), _ctx(premium=False), False),
        (("premium",), _ctx(premium=None), False),
        (("premium",), _ctx(no_account=True), False),
        (("media_bytes",), _ctx(), False),
        (("recent_ids", "premium"), _ctx(recent_ids={"c1": [7]}), False),
        (("recent_ids", "premium"), _ctx(recent_ids={"c1": [7]}, premium=True), True),
    ],
)
def test_is_eligible_needs_matrix(
    needs: tuple[_Need, ...],
    ctx: _ExtraContext,
    expected: bool,  # noqa: FBT001 - table input.
) -> None:
    assert _is_eligible(_spec(*needs), ctx) is expected


# --- budget tiers ------------------------------------------------------------


async def _synthetic_write(ctx: _ExtraContext) -> ActionResult:
    return await _write(ctx, WarmReadContacts())


@pytest.mark.parametrize(
    ("spec", "booked_at_dispatch"),
    [
        (_ExtraSpec("polls", "write", _synthetic_write), 1),
        (EXTRAS[0], 0),
    ],
)
@pytest.mark.asyncio
async def test_writes_book_before_dispatch_and_reads_book_nothing(
    monkeypatch: pytest.MonkeyPatch, spec: _ExtraSpec, booked_at_dispatch: int
) -> None:
    monkeypatch.setattr(_extras, "EXTRAS", (spec,))
    _extras_range(monkeypatch, 1, 1)
    tally = _ChannelTally()
    seen: list[int] = []

    async def _execute(account_id: str, action: TelegramAction) -> ActionResult:
        seen.append(tally.attempts)  # what the dispatcher sees = already booked, or not
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(_seams, "execute", _execute)

    landed = await run_extras_step(_ctx(tally=tally, remaining=None))

    assert landed is True
    assert tally.extras == 1
    assert seen == [booked_at_dispatch]
    assert tally.attempts == booked_at_dispatch


@pytest.mark.asyncio
async def test_exhausted_budget_skips_writes_but_still_dispatches_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _extras, "EXTRAS", (EXTRAS[0], _ExtraSpec("polls", "write", _synthetic_write))
    )
    _extras_range(monkeypatch, 2, 2)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    tally = _ChannelTally()

    landed = await run_extras_step(_ctx(tally=tally, remaining=0))

    assert landed is True
    assert tally.attempts == 0
    assert tally.extras == 1
    assert recorder.types() == ["warm_get_dialogs"]


# --- folding -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "flag"), [("flood_wait", "flooded"), ("peer_flood", "peer_flooded")]
)
@pytest.mark.asyncio
async def test_flood_on_the_nth_extra_halts_the_rest(
    monkeypatch: pytest.MonkeyPatch, status: str, flag: str
) -> None:
    _extras_range(monkeypatch, 5, 5)
    dispatcher = _StatusAt(2, status)
    monkeypatch.setattr(_seams, "execute", dispatcher.execute)
    tally = _ChannelTally()

    landed = await run_extras_step(_ctx(tally=tally))

    assert len(dispatcher.actions) == 2
    assert getattr(tally, flag) is True
    assert tally.last_failed_action == dispatcher.actions[1].action_type
    assert tally.extras == 1
    assert landed is True  # the first one landed, so the rail still advances
    if status == "flood_wait":
        assert tally.flood_seconds == 60
        assert tally.flood_until is not None


@pytest.mark.asyncio
async def test_a_plain_failure_counts_and_the_rest_still_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _extras_range(monkeypatch, 5, 5)
    dispatcher = _StatusAt(1, "failed")
    monkeypatch.setattr(_seams, "execute", dispatcher.execute)
    tally = _ChannelTally()

    landed = await run_extras_step(_ctx(tally=tally))

    assert len(dispatcher.actions) == len(EXTRAS)
    assert tally.failures == 1
    assert tally.extras == len(EXTRAS) - 1
    assert tally.attempts == 0
    assert landed is True


def test_fold_extra_ignores_a_status_outside_every_family() -> None:
    tally = _ChannelTally()
    result = ActionResult(status="already_participant", action_type="x", account_id="acc-1")
    assert _fold_extra(tally, result) is False
    assert (tally.extras, tally.failures, tally.flooded) == (0, 0, False)
    assert tally.last_failed_action is None


@pytest.mark.asyncio
async def test_step_is_skipped_when_the_cycle_is_already_flooded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _extras_range(monkeypatch, 5, 5)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)

    assert await run_extras_step(_ctx(tally=_ChannelTally(flooded=True))) is False
    assert await run_extras_step(_ctx(tally=_ChannelTally(peer_flooded=True))) is False
    assert recorder.actions == []


@pytest.mark.asyncio
async def test_nothing_landed_means_no_rail_step(monkeypatch: pytest.MonkeyPatch) -> None:
    _extras_range(monkeypatch, 1, 1)
    recorder = _Recorder()
    recorder.flood_on = {
        "warm_get_dialogs",
        "warm_read_contacts",
        "warm_read_notify_settings",
        "warm_check_settings",
        "warm_view_profile",
    }
    monkeypatch.setattr(_seams, "execute", recorder.execute)

    assert await run_extras_step(_ctx()) is False
    assert len(recorder.actions) == 1


@pytest.mark.asyncio
async def test_a_runner_returning_none_dispatches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _not_applicable(_ctx: _ExtraContext) -> None:
        return None

    monkeypatch.setattr(_extras, "EXTRAS", (_ExtraSpec("polls", "write", _not_applicable),))
    _extras_range(monkeypatch, 1, 1)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    tally = _ChannelTally()

    assert await run_extras_step(_ctx(tally=tally)) is False
    assert recorder.actions == []
    assert tally.attempts == 0


# --- runners -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_view_profiles_looks_at_a_chosen_channel_or_at_self(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)

    await _extras_reads.view_profiles(_ctx(chosen=[_CHANNEL]))  # rng.random pinned → 0.0
    await _extras_reads.view_profiles(_ctx(chosen=[]))
    monkeypatch.setattr(_seams.rng, "random", lambda: 0.9)
    await _extras_reads.view_profiles(_ctx(chosen=[_CHANNEL]))

    profiles = [a for _id, a in recorder.actions if isinstance(a, WarmViewProfile)]
    assert [(a.kind, a.channel) for a in profiles] == [
        ("channel", "c1"),
        ("self", None),
        ("self", None),
    ]


@pytest.mark.asyncio
async def test_read_runners_draw_client_like_sizes(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)

    await _extras_reads.dialogs(_ctx())
    await _extras_reads.check_settings(_ctx())
    await _extras_reads.notifications(_ctx())

    dialogs, check, notify = (a for _id, a in recorder.actions)
    assert isinstance(dialogs, WarmGetDialogs)
    assert isinstance(check, WarmCheckSettings)
    assert 20 <= dialogs.limit <= 40
    assert 2 <= check.calls <= 4
    assert notify.action_type == "warm_read_notify_settings"


@pytest.mark.asyncio
async def test_check_settings_draws_its_offset_from_the_rng(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first screen comes from ``rng.randrange`` over the whole 11-entry read table."""
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    # Top of the range for every draw (``randint`` routes through ``randrange`` too).
    monkeypatch.setattr(_seams.rng, "randrange", lambda *args: args[-1] - 1)

    await _extras_reads.check_settings(_ctx())

    ((_id, check),) = recorder.actions
    assert isinstance(check, WarmCheckSettings)
    assert check.offset == 10  # the schema's ``le``: the last read is reachable


# --- whole cycle -------------------------------------------------------------


@pytest.mark.asyncio
async def test_extras_run_after_stories_and_dm_and_advance_the_rail_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _extras_range(monkeypatch, 2, 2)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account()
    await _set_settings(chat=False, reactions=False, key="")
    await save_warming_settings(
        gemini_api_key=None,
        extra_toggles={
            **dict.fromkeys(EXTRA_TOGGLE_DEFAULTS, False),
            "dialogs": True,
            "contacts": True,
        },
    )
    steps: list[str] = []

    async def _on_step(step: str) -> None:
        steps.append(step)

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"), on_step=_on_step)

    assert result.status == "ok"
    types = recorder.types()
    warm = [t for t in types if t.startswith("warm_")]
    assert set(warm) == {"warm_get_dialogs", "warm_read_contacts"}
    # Strictly after the story glance, strictly before the closing SetOnline(False).
    assert types.index("watch_peer_stories") < types.index(warm[0])
    assert types[-1] == "set_online"
    assert types[-3:-1] == warm
    assert steps[-1] == "extras"
    assert steps.index("stories") < steps.index("extras")
    # Free reads: every dispatched action booked budget except the two extras (the
    # closing SetOnline(False) is cleanup and never booked either).
    assert result.attempted_actions == len(types) - 1 - len(warm)
