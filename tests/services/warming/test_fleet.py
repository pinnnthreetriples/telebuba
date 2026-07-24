"""Warming tests split from the former service test module: test_fleet.py."""

from __future__ import annotations

import random

import pytest

from core.config import settings
from services import warming
from services.warming import _fleet, _seams
from services.warming.pacing import (
    persona_dm_probability,
    persona_reaction_probability,
)
from tests.services.warming._support import (
    _MON,
    _SAT,
    _affinity_pool,
    _configure_intensity,
)


def test_channel_affinity_is_stable_per_account(monkeypatch: pytest.MonkeyPatch) -> None:
    # A human's interests don't reshuffle each session: the same account + pool
    # yields the same affinity subset across cycles (a process-stable seed).
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 1)
    monkeypatch.setattr(settings.warming, "channel_affinity_ratio", 0.5)
    pool = _affinity_pool(10)

    first = _fleet._account_channel_affinity("acc-1", pool)
    second = _fleet._account_channel_affinity("acc-1", pool)

    assert [c.channel for c in first] == [c.channel for c in second]


def test_channel_affinity_differs_between_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    # Two accounts carve different slices of the shared pool → their join/read
    # graphs de-correlate (partial overlap is fine, identical subsets are the tell).
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 1)
    monkeypatch.setattr(settings.warming, "channel_affinity_ratio", 0.5)
    pool = _affinity_pool(10)

    subsets = {
        frozenset(c.channel for c in _fleet._account_channel_affinity(f"acc-{i}", pool))
        for i in range(6)
    }

    assert all(len(s) == 5 for s in subsets)
    assert len(subsets) > 1  # not every account landed on the same 5 channels


def test_channel_affinity_whole_pool_when_small(monkeypatch: pytest.MonkeyPatch) -> None:
    # A pool no larger than one cycle's floor has nothing to subdivide → the whole
    # pool is the affinity, so tiny-pool behaviour is unchanged (no regression).
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 2)
    monkeypatch.setattr(settings.warming, "channel_affinity_ratio", 0.5)

    for size in (1, 2):
        pool = _affinity_pool(size)
        affinity = _fleet._account_channel_affinity("acc-1", pool)
        assert {c.channel for c in affinity} == {c.channel for c in pool}


def test_channel_affinity_ratio_controls_size(monkeypatch: pytest.MonkeyPatch) -> None:
    # The config ratio sets the slice size: a bigger ratio → a bigger interest set.
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 1)
    pool = _affinity_pool(20)

    monkeypatch.setattr(settings.warming, "channel_affinity_ratio", 0.25)
    small = _fleet._account_channel_affinity("acc-1", pool)
    monkeypatch.setattr(settings.warming, "channel_affinity_ratio", 0.75)
    large = _fleet._account_channel_affinity("acc-1", pool)

    assert len(small) == 5
    assert len(large) == 15


def test_channel_affinity_honors_min_under_low_ratio(monkeypatch: pytest.MonkeyPatch) -> None:
    # #203 P3.6: a low ratio must never carve a subset too small to draw a cycle's
    # floor — round(20*0.05)=1 but channels_per_cycle_min=3 raises it to 3.
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 3)
    monkeypatch.setattr(settings.warming, "channel_affinity_ratio", 0.05)
    pool = _affinity_pool(20)

    assert len(_fleet._account_channel_affinity("acc-1", pool)) == 3


def test_channel_affinity_frozen_when_churn_strength_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    # churn_strength=0 → the interest subset is identical across every epoch.
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 1)
    monkeypatch.setattr(settings.warming, "channel_affinity_ratio", 0.25)
    monkeypatch.setattr(settings.warming, "channel_affinity_churn_strength", 0.0)
    pool = _affinity_pool(20)

    sets = {
        frozenset(c.channel for c in _fleet._account_channel_affinity("acc-1", pool, epoch))
        for epoch in range(6)
    }

    assert len(sets) == 1  # frozen across epochs
    assert len(next(iter(sets))) == 5


def test_channel_affinity_churns_slowly_over_epochs(monkeypatch: pytest.MonkeyPatch) -> None:
    # #203: with churn on, membership drifts across epochs (union > one slice) but
    # slowly — adjacent epochs still share most of the set, not a wholesale swap.
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 1)
    monkeypatch.setattr(settings.warming, "channel_affinity_ratio", 0.25)
    monkeypatch.setattr(settings.warming, "channel_affinity_churn_strength", 0.15)
    pool = _affinity_pool(40)

    sets = [
        frozenset(c.channel for c in _fleet._account_channel_affinity("acc-1", pool, epoch))
        for epoch in range(30)
    ]

    assert all(len(s) == 10 for s in sets)  # size preserved every epoch
    assert len(frozenset().union(*sets)) > 10  # membership drifts over time
    assert all(len(sets[i] & sets[i + 1]) >= 5 for i in range(len(sets) - 1))  # ...gradually


def test_channel_affinity_churn_strength_controls_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    # A gentle strength keeps more of the previous epoch's set than a violent one.
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 1)
    monkeypatch.setattr(settings.warming, "channel_affinity_ratio", 0.25)
    pool = _affinity_pool(40)

    def subset(strength: float, epoch: int) -> frozenset[str]:
        monkeypatch.setattr(settings.warming, "channel_affinity_churn_strength", strength)
        return frozenset(c.channel for c in _fleet._account_channel_affinity("acc-1", pool, epoch))

    base = subset(0.1, 0)
    gentle = subset(0.1, 1)
    violent = subset(0.9, 1)

    assert len(base) == len(gentle) == len(violent) == 10
    assert len(base & gentle) >= len(base & violent)


def test_channel_affinity_memoized_within_epoch(monkeypatch: pytest.MonkeyPatch) -> None:
    # M2: the subset is a pure function of (account, epoch, channel set, config),
    # so a repeat call in the same epoch is served from the memo — the same list
    # object, no re-hash/re-sort.
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 1)
    monkeypatch.setattr(settings.warming, "channel_affinity_ratio", 0.5)
    pool = _affinity_pool(10)

    first = _fleet._account_channel_affinity("acc-1", pool)
    second = _fleet._account_channel_affinity("acc-1", pool)

    assert second is first  # cache hit, not a recompute
    assert [c.channel for c in first] == [c.channel for c in second]


def test_channel_affinity_cache_busts_across_epochs(monkeypatch: pytest.MonkeyPatch) -> None:
    # A different epoch is a different cache key, so the memo must not serve one
    # epoch's slice for another — each epoch is computed independently. The new
    # epoch also evicts the old one so the cache stays bounded to the live epoch.
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 1)
    monkeypatch.setattr(settings.warming, "channel_affinity_churn_strength", 0.9)
    monkeypatch.setattr(settings.warming, "channel_affinity_ratio", 0.25)
    pool = _affinity_pool(40)

    e0 = _fleet._account_channel_affinity("acc-1", pool, 0)
    e1 = _fleet._account_channel_affinity("acc-1", pool, 1)

    assert e0 is not e1  # distinct keys → separately computed, not a shared hit
    assert [c.channel for c in e0] != [c.channel for c in e1]  # drift changed the slice
    # Prior-epoch entry was evicted: only the live epoch survives.
    assert all(key[1] == 1 for key in _fleet._AFFINITY_CACHE)


def test_channel_affinity_cache_busts_on_config_change(monkeypatch: pytest.MonkeyPatch) -> None:
    # The memo key folds in the selection config, so changing it mid-run must
    # recompute rather than serve a stale slice sized for the old config.
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 1)
    monkeypatch.setattr(settings.warming, "channel_affinity_ratio", 0.25)
    pool = _affinity_pool(40)

    narrow = _fleet._account_channel_affinity("acc-1", pool, 0)
    monkeypatch.setattr(settings.warming, "channel_affinity_ratio", 0.5)
    wide = _fleet._account_channel_affinity("acc-1", pool, 0)

    assert wide is not narrow
    assert len(wide) > len(narrow)  # the new ratio actually drove a fresh compute


def test_maybe_explore_swaps_in_off_affinity_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    # #203: with the exploration roll passing, one chosen channel is replaced by a
    # channel from outside the affinity set (count preserved).
    monkeypatch.setattr(settings.warming, "channel_exploration_probability", 1.0)
    pool = _affinity_pool(6)
    affinity = pool[:3]
    chosen = pool[:2]

    result = _fleet._maybe_explore(chosen, pool, affinity, "acc-1", _seams.rng)

    off_names = {c.channel for c in pool[3:]}
    assert len(result) == len(chosen)
    assert any(c.channel in off_names for c in result)  # a fresh interest crept in
    # the swapped-in channel is the account's stable top off-affinity pick, not a
    # uniform shared draw — so its permanent join stays de-correlated across the fleet.
    expected = min(pool[3:], key=lambda c: _fleet._stable_fraction(f"aff:acc-1:{c.channel}"))
    assert any(c.channel == expected.channel for c in result)


def test_maybe_explore_pick_is_decorrelated_across_fleet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # #203: different accounts explore *different* off-affinity channels (each its
    # own stable top secondary interest), so the permanent joins exploration
    # triggers don't drift the fleet toward one shared membership graph.
    monkeypatch.setattr(settings.warming, "channel_exploration_probability", 1.0)
    pool = _affinity_pool(30)
    affinity = pool[:5]
    affinity_names = {a.channel for a in affinity}
    picks = {
        next(
            c.channel
            for c in _fleet._maybe_explore(pool[:2], pool, affinity, f"acc-{i}", _seams.rng)
            if c.channel not in affinity_names
        )
        for i in range(12)
    }

    assert len(picks) > 1  # not every account lands on the same off-affinity channel


def test_maybe_explore_noop_when_probability_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    # Probability 0 (fixture pins rng.random→0.0, and 0.0 >= 0.0) → chosen is left
    # exactly as-is.
    monkeypatch.setattr(settings.warming, "channel_exploration_probability", 0.0)
    pool = _affinity_pool(6)

    assert _fleet._maybe_explore(pool[:2], pool, pool[:3], "acc-1", _seams.rng) == pool[:2]


def test_maybe_explore_noop_when_no_off_affinity(monkeypatch: pytest.MonkeyPatch) -> None:
    # Whole pool is the affinity (nothing outside) → exploration can't swap even
    # when the roll fires; an empty chosen list is likewise left untouched.
    monkeypatch.setattr(settings.warming, "channel_exploration_probability", 1.0)
    pool = _affinity_pool(3)

    assert _fleet._maybe_explore(pool, pool, pool, "acc-1", _seams.rng) == pool
    assert _fleet._maybe_explore([], pool, pool, "acc-1", _seams.rng) == []


def test_is_quiet_day_false_on_unparseable_date(monkeypatch: pytest.MonkeyPatch) -> None:
    # A malformed daily_count_date must not raise — treat it as an active day.
    monkeypatch.setattr(settings.warming, "quiet_day_weekday_probability", 1.0)
    monkeypatch.setattr(settings.warming, "quiet_day_weekend_probability", 1.0)

    assert _fleet._is_quiet_day("acc-1", "not-a-date") is False


def test_compute_intensity_ceiling_for_fresh_account(monkeypatch: pytest.MonkeyPatch) -> None:
    # The age-ramp is retired: channel range is the flat config, and a fresh
    # account is throttled by the phase cap + the DM cold-start guard, not by a
    # per-cycle ramp. Intro phase, cap 3, DM blocked under dm_min_age.
    _configure_intensity(monkeypatch)
    fresh = warming.compute_intensity(0.0)
    assert fresh.channels_max == 3
    assert fresh.phase == "intro"
    assert fresh.daily_cap == 3
    assert fresh.dm_allowed is False


def test_compute_intensity_dm_unlocks_at_min_age(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_intensity(monkeypatch)
    # DM unlocks exactly at the cold-start threshold; channel range stays flat.
    assert warming.compute_intensity(36.0).dm_allowed is True
    assert warming.compute_intensity(35.0).dm_allowed is False
    assert warming.compute_intensity(500.0).channels_max == 3


def test_compute_intensity_dm_gated_by_trust_band(monkeypatch: pytest.MonkeyPatch) -> None:
    # DM permission depends on trust band, not age alone (audit П11): only
    # excellent/good/watch may DM; at_risk/critical may not.
    _configure_intensity(monkeypatch)
    assert warming.compute_intensity(500.0, trust_band="good").dm_allowed is True
    assert warming.compute_intensity(500.0, trust_band="watch").dm_allowed is True
    assert warming.compute_intensity(500.0, trust_band="at_risk").dm_allowed is False
    assert warming.compute_intensity(500.0, trust_band="critical").dm_allowed is False
    # A healthy band cannot un-block DM for a too-young account.
    assert warming.compute_intensity(0.0, trust_band="good").dm_allowed is False
    # No band passed → age-only, so direct callers (run_one_cycle) are unchanged.
    assert warming.compute_intensity(500.0).dm_allowed is True


def test_persona_presets_scale_reaction_and_dm() -> None:
    # Persona sets per-session frequency; calm < normal < active for both levers.
    assert (
        persona_reaction_probability("calm")
        < persona_reaction_probability("normal")
        < persona_reaction_probability("active")
    )
    assert (
        persona_dm_probability("calm")
        < persona_dm_probability("normal")
        < persona_dm_probability("active")
    )


def test_persona_next_run_seconds_capped_by_phase_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    # A tiny daily cap (young account) forces one long gap regardless of the
    # persona's headline sessions/day — the phase ceiling throttles cadence.
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 23)
    monkeypatch.setattr(settings.warming, "next_run_jitter_fraction", 0.0)
    rng = random.Random(1)  # noqa: S311 - deterministic test rng
    # intro cap 3 affords 0 sessions → floored to 1 → gap == the full 15-h window.
    gap = warming.persona_next_run_seconds("active", 3, rng)
    assert gap == pytest.approx(15 * 3600)


def test_is_quiet_day_stable_per_account_and_day(monkeypatch: pytest.MonkeyPatch) -> None:
    # #203: the quiet-day verdict is decided once per calendar day (stable), and
    # the fleet doesn't all rest on the same day.
    monkeypatch.setattr(settings.warming, "quiet_day_weekday_probability", 0.5)
    monkeypatch.setattr(settings.warming, "quiet_day_weekend_probability", 0.5)

    assert _fleet._is_quiet_day("acc-1", _MON) == _fleet._is_quiet_day("acc-1", _MON)
    verdicts = {_fleet._is_quiet_day(f"acc-{i}", _MON) for i in range(20)}
    assert verdicts == {True, False}  # some accounts rest, some don't


def test_is_quiet_day_weekend_biased(monkeypatch: pytest.MonkeyPatch) -> None:
    # Weekday prob 0, weekend prob 1 → never quiet on a weekday, always on a weekend.
    monkeypatch.setattr(settings.warming, "quiet_day_weekday_probability", 0.0)
    monkeypatch.setattr(settings.warming, "quiet_day_weekend_probability", 1.0)

    assert _fleet._is_quiet_day("acc-1", _MON) is False
    assert _fleet._is_quiet_day("acc-1", _SAT) is True


def test_is_quiet_day_disabled_when_probability_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "quiet_day_weekday_probability", 0.0)
    monkeypatch.setattr(settings.warming, "quiet_day_weekend_probability", 0.0)

    assert _fleet._is_quiet_day("acc-1", _MON) is False
    assert _fleet._is_quiet_day("acc-1", _SAT) is False
