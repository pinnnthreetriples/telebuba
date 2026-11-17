"""T4: the fold, one row of design table 5.3 and each rule of 5.4 at a time."""

from __future__ import annotations

from datetime import timedelta

import pytest

from schemas.spam_evidence import Evidence, EvidenceKind, Verdict
from services.spam_intel._evidence_policy import KIND_POLICY, strength
from services.spam_intel._fold import bucket, fold
from tests.services.spam_intel._evidence_support import CFG, NOW, ev

type Expect = dict[str, object]


def _check(items: list[Evidence], expected: Expect) -> Verdict:
    verdict = fold(items, NOW, CFG)
    actual = {name: getattr(verdict, name) for name in expected}
    assert actual == expected
    return verdict


# One case per row of the table: kind, age, and what the fold says about it alone.
_ROWS: list[tuple[str, list[Evidence], Expect]] = [
    (
        "spambot_limited undated",
        [ev("spambot_limited", 2)],
        {"status": "limited", "confidence": "high", "primary_kind": "spambot_limited"},
    ),
    (
        "spambot_limited undated never ages",
        [ev("spambot_limited", 100)],
        {"status": "limited", "confidence": "high", "needs_probe": False, "reason": None},
    ),
    (
        "spambot_limited undated is rechecked after five days",
        [ev("spambot_limited", 24 * 5 + 1)],
        {"status": "limited", "confidence": "high", "needs_probe": True, "reason": "recheck_due"},
    ),
    (
        "spambot_limited dated and still running",
        [ev("spambot_limited", 2, expires_in_hours=30)],
        {
            "status": "limited",
            "valid_until": NOW + timedelta(hours=30),
            "needs_probe": False,
            "reason": None,
        },
    ),
    (
        "spambot_limited dated and over: unknown, ask again",
        [ev("spambot_limited", 50, expires_in_hours=-1)],
        {"status": "unknown", "needs_probe": True, "reason": "limit_expired", "confidence": "low"},
    ),
    (
        "spambot_limited expires exactly now",
        [ev("spambot_limited", 50, expires_in_hours=0)],
        {"status": "unknown", "reason": "limit_expired"},
    ),
    (
        "spambot_clean fresh",
        [ev("spambot_clean", 1)],
        {"status": "clean", "confidence": "high", "primary_kind": "spambot_clean", "reason": None},
    ),
    (
        "spambot_clean after one half-life is medium and not yet stale",
        [ev("spambot_clean", 120)],
        {"status": "clean", "confidence": "medium", "reason": None},
    ),
    (
        "spambot_clean after two half-lives is low and stale",
        [ev("spambot_clean", 240)],
        {"status": "clean", "confidence": "low", "reason": "stale"},
    ),
    (
        "spambot_unreadable alone",
        [ev("spambot_unreadable", 1)],
        {"status": "unknown", "reason": "unreadable", "needs_probe": False},
    ),
    (
        "spambot_reviewing within its six hours",
        [ev("spambot_reviewing", 1, expires_in_hours=5)],
        {"status": "unknown", "reason": "reviewing"},
    ),
    (
        "spambot_reviewing after its six hours",
        [ev("spambot_reviewing", 7, expires_in_hours=-1)],
        {"status": "unknown", "reason": "no_evidence"},
    ),
    (
        "peer_flood_send fresh",
        [ev("peer_flood_send", 0)],
        {"status": "limited", "confidence": "medium", "primary_kind": "peer_flood_send"},
    ),
    (
        "peer_flood_send after its half-life",
        [ev("peer_flood_send", 72)],
        {"status": "limited", "confidence": "low"},
    ),
    (
        "peer_flood_other fresh is weaker",
        [ev("peer_flood_other", 0)],
        {"status": "limited", "confidence": "medium", "primary_kind": "peer_flood_other"},
    ),
    (
        "peer_flood_other after its half-life",
        [ev("peer_flood_other", 48)],
        {"status": "limited", "confidence": "low"},
    ),
    (
        "user_banned_in_channel",
        [ev("user_banned_in_channel", 1)],
        {"status": "limited", "primary_kind": "user_banned_in_channel", "confidence": "medium"},
    ),
    (
        "dm_stranger_ok fresh",
        [ev("dm_stranger_ok", 1)],
        {"status": "clean", "confidence": "high", "primary_kind": "dm_stranger_ok"},
    ),
    (
        "dm_stranger_ok after its half-life",
        [ev("dm_stranger_ok", 48)],
        {"status": "clean", "confidence": "medium"},
    ),
    (
        "flood_wait is not evidence",
        [ev("flood_wait", 0)],
        {"status": "unknown", "reason": "no_evidence", "needs_probe": False},
    ),
    (
        "nothing at all",
        [],
        {"status": "unknown", "reason": "no_evidence", "primary_kind": None},
    ),
]


@pytest.mark.parametrize(
    ("items", "expected"), [row[1:] for row in _ROWS], ids=[r[0] for r in _ROWS]
)
def test_each_row_of_the_table_alone(items: list[Evidence], expected: Expect) -> None:
    _check(items, expected)


@pytest.mark.parametrize("kind", ["group_send_ok", "join_ok", "read_ok", "dm_known_peer_ok"])
def test_shadow_kinds_alone_say_nothing(kind: EvidenceKind) -> None:
    assert KIND_POLICY[kind].base == 0
    verdict = _check([ev(kind, 0)], {"status": "unknown", "reason": "no_evidence"})
    assert verdict.weights_used == {kind: 0}


@pytest.mark.parametrize("kind", ["group_send_ok", "join_ok", "read_ok", "dm_known_peer_ok"])
def test_a_newer_shadow_success_never_contradicts_or_lifts_a_limit(kind: EvidenceKind) -> None:
    _check(
        [ev("spambot_limited", 5), ev(kind, 1)],
        {"status": "limited", "contradiction": False, "needs_probe": False},
    )


# The supersede rule: a clean item that is newer and whose base is not lower ends a limit.
_SUPERSEDE: list[tuple[str, list[Evidence], Expect]] = [
    (
        "a newer @SpamBot clean supersedes an older @SpamBot limit",
        [ev("spambot_limited", 10), ev("spambot_clean", 1)],
        {"status": "clean", "primary_kind": "spambot_clean", "contradiction": False},
    ),
    (
        "a newer @SpamBot clean supersedes an older PeerFlood",
        [ev("peer_flood_send", 10), ev("spambot_clean", 1)],
        {"status": "clean"},
    ),
    (
        "a newer first-message proof supersedes an older PeerFlood",
        [ev("peer_flood_send", 10), ev("dm_stranger_ok", 1)],
        {"status": "clean", "primary_kind": "dm_stranger_ok", "contradiction": False},
    ),
    (
        "a newer first-message proof supersedes an older channel ban",
        [ev("user_banned_in_channel", 10), ev("dm_stranger_ok", 1)],
        {"status": "clean"},
    ),
    (
        "a newer first-message proof does NOT supersede an older @SpamBot limit",
        [ev("spambot_limited", 10), ev("dm_stranger_ok", 1)],
        {
            "status": "limited",
            "primary_kind": "spambot_limited",
            "contradiction": True,
            "needs_probe": True,
            "reason": "contradiction",
        },
    ),
    (
        "an older clean never supersedes a newer limit",
        [ev("spambot_clean", 10), ev("spambot_limited", 1)],
        {"status": "limited", "contradiction": False, "needs_probe": False},
    ),
    (
        "an older first-message proof loses to a newer PeerFlood",
        [ev("dm_stranger_ok", 10), ev("peer_flood_send", 1)],
        {"status": "limited", "contradiction": False},
    ),
    (
        "equal instants are not newer: the limit stands",
        [ev("spambot_limited", 3), ev("spambot_clean", 3)],
        {"status": "limited", "contradiction": False, "needs_probe": False},
    ),
    (
        "a clean ends only the limits older than it",
        [ev("peer_flood_send", 10), ev("spambot_clean", 5), ev("peer_flood_send", 1)],
        {"status": "limited", "contradiction": False},
    ),
]


@pytest.mark.parametrize(
    ("items", "expected"), [row[1:] for row in _SUPERSEDE], ids=[r[0] for r in _SUPERSEDE]
)
def test_the_supersede_rule(items: list[Evidence], expected: Expect) -> None:
    _check(items, expected)


def test_the_strongest_limit_decides_and_the_clock_orders_the_rest() -> None:
    verdict = _check(
        [ev("peer_flood_send", 1), ev("spambot_limited", 90), ev("user_banned_in_channel", 2)],
        {"status": "limited", "primary_kind": "spambot_limited", "confidence": "high"},
    )
    assert verdict.valid_until is None  # nothing says when it ends


@pytest.mark.parametrize(
    ("items", "until_hours"),
    [
        (
            [
                ev("spambot_limited", 1, expires_in_hours=10),
                ev("spambot_limited", 2, expires_in_hours=40),
            ],
            40,
        ),
        ([ev("spambot_limited", 1, expires_in_hours=10), ev("peer_flood_send", 2)], None),
    ],
    ids=["every limit dated: the latest date", "one undated limit: no end in sight"],
)
def test_valid_until_is_when_the_limited_state_ends(
    items: list[Evidence], until_hours: float | None
) -> None:
    expected = None if until_hours is None else NOW + timedelta(hours=until_hours)
    assert fold(items, NOW, CFG).valid_until == expected


# A dated limit that ran out contributes nothing, and blocks a clean reading older than it.
@pytest.mark.parametrize(
    ("items", "expected"),
    [
        (
            [ev("spambot_clean", 60), ev("spambot_limited", 50, expires_in_hours=-1)],
            {"status": "unknown", "reason": "limit_expired", "needs_probe": True},
        ),
        (
            [ev("spambot_limited", 60, expires_in_hours=-30), ev("spambot_clean", 5)],
            {"status": "clean", "reason": None, "needs_probe": False},
        ),
        (
            [ev("spambot_limited", 50, expires_in_hours=-1), ev("peer_flood_send", 1)],
            {"status": "limited", "primary_kind": "peer_flood_send"},
        ),
        (
            [ev("spambot_limited", 50, expires_in_hours=-1), ev("dm_stranger_ok", 1)],
            {"status": "clean", "primary_kind": "dm_stranger_ok"},
        ),
    ],
    ids=[
        "an older clean cannot stand in for an account whose limit just ended",
        "a clean newer than the ended limit stands",
        "an ended limit does not hide a fresh PeerFlood",
        "an ended limit followed by a delivered first message is clean",
    ],
)
def test_an_ended_dated_limit(items: list[Evidence], expected: Expect) -> None:
    _check(items, expected)


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        ([ev("freeze_seen", 5)], {"status": "frozen", "confidence": "high", "reason": "frozen"}),
        (
            [ev("freeze_seen", 5), ev("spambot_clean", 1), ev("dm_stranger_ok", 0)],
            {"status": "frozen", "primary_kind": "freeze_seen"},
        ),
        (
            [ev("freeze_seen", 5), ev("peer_flood_send", 1)],
            {"status": "frozen"},
        ),
        (
            [ev("freeze_seen", 5), ev("freeze_cleared", 1)],
            {"status": "unknown", "reason": "no_evidence"},
        ),
        (
            [ev("freeze_seen", 5, detail="2026-12-01"), ev("freeze_cleared", 9)],
            {"status": "frozen"},
        ),
        (
            [ev("freeze_seen", 9), ev("freeze_cleared", 5), ev("freeze_seen", 1)],
            {"status": "frozen"},
        ),
        (
            [ev("freeze_seen", 9), ev("freeze_cleared", 5), ev("spambot_clean", 1)],
            {"status": "clean"},
        ),
    ],
    ids=[
        "freeze_seen alone",
        "no clean evidence of any kind overrides it",
        "nor does a limit: frozen is its own verdict",
        "freeze_cleared closes it",
        "a freeze_cleared OLDER than the freeze closes nothing",
        "a freeze seen again after it was cleared",
        "after the closer the spam evidence speaks again",
    ],
)
def test_frozen_is_closed_only_by_freeze_cleared(items: list[Evidence], expected: Expect) -> None:
    _check(items, expected)


def test_freeze_until_date_is_detail_only() -> None:
    verdict = fold([ev("freeze_seen", 1, detail="2026-12-01", expires_in_hours=None)], NOW, CFG)
    assert (verdict.status, verdict.valid_until) == ("frozen", None)


@pytest.mark.parametrize(
    ("items", "flag"),
    [
        ([ev("tos_restricted", 5, detail="porn-all")], (True, "porn-all")),
        ([ev("tos_restricted", 5, detail="x"), ev("tos_cleared", 1)], (False, None)),
        ([ev("tos_cleared", 9), ev("tos_restricted", 1, detail="y")], (True, "y")),
        ([], (False, None)),
    ],
    ids=["seen", "closed by tos_cleared", "seen again after a closer", "never"],
)
def test_tos_restricted_is_a_flag_with_its_reason(
    items: list[Evidence], flag: tuple[bool, str | None]
) -> None:
    verdict = fold(items, NOW, CFG)
    assert (verdict.tos_restricted, verdict.tos_reason) == flag


@pytest.mark.parametrize(
    "others",
    [[], [ev("spambot_limited", 3)], [ev("spambot_clean", 3)], [ev("freeze_seen", 3)]],
    ids=["alone", "beside a limit", "beside a clean", "beside a freeze"],
)
def test_tos_restricted_never_moves_the_verdict(others: list[Evidence]) -> None:
    without = fold(others, NOW, CFG)
    with_tos = fold([*others, ev("tos_restricted", 1, detail="r")], NOW, CFG)
    assert with_tos.model_copy(update={"tos_restricted": False, "tos_reason": None}).model_dump(
        exclude={"weights_used"}
    ) == without.model_dump(exclude={"weights_used"})
    assert with_tos.tos_restricted is True


def test_last_spambot_at_is_the_latest_answer_of_any_kind() -> None:
    items = [ev("spambot_clean", 30), ev("spambot_unreadable", 4), ev("peer_flood_send", 1)]
    assert fold(items, NOW, CFG).last_spambot_at == NOW - timedelta(hours=4)
    assert fold([ev("peer_flood_send", 1)], NOW, CFG).last_spambot_at is None


def test_the_fold_reports_the_base_weight_it_used_per_kind() -> None:
    verdict = fold([ev("peer_flood_send", 1), ev("dm_stranger_ok", 2), ev("join_ok", 3)], NOW, CFG)
    assert verdict.weights_used == {"peer_flood_send": 0.7, "dm_stranger_ok": 0.8, "join_ok": 0.0}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.0, "high"),
        (0.75, "high"),
        (0.7499, "medium"),
        (0.4, "medium"),
        (0.3999, "low"),
        (0, "low"),
    ],
)
def test_confidence_buckets(value: float, expected: str) -> None:
    assert bucket(value) == expected


def test_strength_decays_by_half_per_half_life_and_stops_at_expiry() -> None:
    clean = ev("spambot_clean", 120)
    assert strength(clean, NOW) == pytest.approx(0.5)
    assert strength(ev("spambot_clean", 0, expires_in_hours=-0.001), NOW) == 0
    assert strength(ev("spambot_limited", 10_000), NOW) == 1.0  # no half-life
    # An item stamped after ``now`` (clock skew) is no stronger than its base.
    assert strength(ev("spambot_clean", -5), NOW) == 1.0


def test_the_recheck_period_is_a_setting() -> None:
    quick = CFG.model_copy(update={"limited_recheck_days": 1.0})
    items = [ev("spambot_limited", 25)]
    assert fold(items, NOW, quick).reason == "recheck_due"
    assert fold(items, NOW, CFG).reason is None


def test_a_naive_instant_is_read_as_utc() -> None:
    naive = Evidence(
        account_id="a1",
        kind="spambot_clean",
        direction="clean",
        observed_at=(NOW - timedelta(hours=1)).replace(tzinfo=None),
        source="test",
    )
    assert naive.observed_at.tzinfo is not None
    assert fold([naive], NOW, CFG).status == "clean"
