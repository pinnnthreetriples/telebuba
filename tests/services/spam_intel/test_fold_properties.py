"""T4: the properties of the fold (design 5.4), checked on random evidence with ``hypothesis``."""

from __future__ import annotations

from datetime import timedelta
from typing import get_args

from hypothesis import given, settings
from hypothesis import strategies as st

from schemas.spam_evidence import Evidence, EvidenceKind
from services.spam_intel._evidence_policy import KIND_POLICY, strength
from services.spam_intel._fold import fold
from tests.services.spam_intel._evidence_support import CFG, NOW, ev

_KINDS = get_args(EvidenceKind)
_RANK = {"low": 0, "medium": 1, "high": 2}
_PROFILE = settings(max_examples=150, deadline=None)


@st.composite
def _item(draw: st.DrawFn, kinds: tuple[str, ...] = _KINDS) -> Evidence:
    expiry = draw(st.one_of(st.none(), st.floats(min_value=-60, max_value=60)))
    return ev(
        draw(st.sampled_from(kinds)),  # ty: ignore[invalid-argument-type]
        draw(st.floats(min_value=0, max_value=900)),
        expires_in_hours=expiry,
        source=draw(st.sampled_from(["a", "b"])),
        detail=draw(st.one_of(st.none(), st.sampled_from(["x", "y", "z"]))),
    )


_EVIDENCE = st.lists(_item(), max_size=12)
_NON_CLOSING = tuple(kind for kind in _KINDS if kind != "freeze_cleared")


def _dump(items: list[Evidence]) -> dict[str, object]:
    return fold(items, NOW, CFG).model_dump()


@_PROFILE
@given(_EVIDENCE, st.randoms(use_true_random=False))
def test_the_order_of_the_evidence_does_not_matter(items: list[Evidence], rnd) -> None:
    shuffled = list(items)
    rnd.shuffle(shuffled)
    assert _dump(shuffled) == _dump(items)


@_PROFILE
@given(_EVIDENCE)
def test_the_fold_is_idempotent_and_repeats_change_nothing(items: list[Evidence]) -> None:
    assert _dump(items) == _dump(items)
    assert _dump([*items, *items]) == _dump(items)


@_PROFILE
@given(_EVIDENCE, _item(), st.floats(min_value=1, max_value=500))
def test_older_and_weaker_evidence_never_changes_the_status(
    items: list[Evidence], extra: Evidence, older_by_hours: float
) -> None:
    verdict = fold(items, NOW, CFG)
    if verdict.status == "unknown" or not items:
        return
    oldest = min(item.observed_at for item in items)
    older = extra.model_copy(update={"observed_at": oldest - timedelta(hours=older_by_hours)})
    older.last_seen_at = older.observed_at
    pool = [item for item in items if KIND_POLICY[item.kind].direction == verdict.status]
    winner = max((strength(item, NOW) for item in pool), default=1.0)
    if verdict.status == "frozen":
        winner = 1.0
    if strength(older, NOW) < winner:
        assert fold([*items, older], NOW, CFG).status == verdict.status


@_PROFILE
@given(_EVIDENCE)
def test_a_newer_spambot_clean_never_leaves_limited_standing(items: list[Evidence]) -> None:
    newest = max((item.observed_at for item in items), default=NOW)
    clean = ev("spambot_clean", 0).model_copy(update={"observed_at": newest + timedelta(seconds=1)})
    assert fold([*items, clean], NOW, CFG).status in {"clean", "frozen"}


@_PROFILE
@given(_EVIDENCE)
def test_clean_needs_clean_evidence_with_strength(items: list[Evidence]) -> None:
    if fold(items, NOW, CFG).status != "clean":
        return
    assert any(
        KIND_POLICY[item.kind].direction == "clean"
        and KIND_POLICY[item.kind].base > 0
        and strength(item, NOW) > 0
        for item in items
    )


@_PROFILE
@given(_EVIDENCE, st.floats(min_value=0, max_value=400))
def test_confidence_never_rises_as_time_passes(items: list[Evidence], later_hours: float) -> None:
    before = fold(items, NOW, CFG)
    after = fold(items, NOW + timedelta(hours=later_hours), CFG)
    assert _RANK[after.confidence] <= _RANK[before.confidence]


@_PROFILE
@given(_EVIDENCE)
def test_a_probe_is_never_asked_for_by_a_confident_unflagged_verdict(items: list[Evidence]) -> None:
    verdict = fold(items, NOW, CFG)
    if verdict.needs_probe and verdict.confidence == "high" and not verdict.contradiction:
        # The one confident reason: a limit with no end date has gone unchecked for days.
        assert verdict.reason == "recheck_due"


@_PROFILE
@given(st.lists(_item(_NON_CLOSING), max_size=12), _item(("freeze_seen",)))
def test_frozen_is_never_overridden_except_by_freeze_cleared(
    items: list[Evidence], freeze: Evidence
) -> None:
    assert fold([*items, freeze], NOW, CFG).status == "frozen"
    closer = ev("freeze_cleared", 0).model_copy(
        update={"observed_at": freeze.observed_at + timedelta(seconds=1)}
    )
    assert fold([*items, freeze, closer], NOW, CFG).status != "frozen" or any(
        item.kind == "freeze_seen" and item.observed_at >= closer.observed_at for item in items
    )


@_PROFILE
@given(_EVIDENCE)
def test_weightless_kinds_never_move_the_status(items: list[Evidence]) -> None:
    weightless = [i for i in items if KIND_POLICY[i.kind].base == 0 and i.kind != "spambot_clean"]
    weighted = [item for item in items if item not in weightless]
    assert fold(items, NOW, CFG).status == fold(weighted, NOW, CFG).status
