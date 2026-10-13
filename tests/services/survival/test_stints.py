"""Journal rows → stints: lives cut at deletions, stints opened by new starts only."""

from __future__ import annotations

from services.survival._stints import build_stints, parse_moment
from tests.services.survival._support import at, iso, limited, released_life, row


def test_a_resume_with_the_same_start_opens_no_second_stint() -> None:
    events = [
        *released_life(14),
        row("warming_started", 20, stint=0, new_stint=False),
        row("promoted", 25, stint=0),
    ]

    [stint] = build_stints(events)

    assert stint.started_at == at(0)
    assert len(stint.events) == len(events)
    assert stint.current
    assert stint.cut_at is None


def test_a_fresh_start_cuts_the_previous_stint() -> None:
    events = [*released_life(14), row("warming_started", 30, stint=30, new_stint=True)]

    first, second = build_stints(events)

    assert first.cut_at == at(30)
    assert not first.current
    assert second.current
    assert second.started_at == at(30)


def test_a_repeated_id_after_a_deletion_is_a_new_life() -> None:
    # The first account died and was deleted; a re-import under the same id warms again.
    events = [
        *released_life(14),
        limited(16),
        row("deleted", 17, reason="dead"),
        row("warming_started", 40, stint=40, new_stint=True),
    ]

    old, new = build_stints(events)

    assert not old.current
    assert old.events[-1].kind == "deleted"
    assert new.current
    assert all(event.kind != "spambot_verdict" for event in new.life_events)


def test_a_row_naming_an_unopened_stint_opens_it() -> None:
    # The start fell out of the log retention before the journal existed.
    [stint] = build_stints([row("warmed", 14, stint=0), row("spambot_verdict", 15, status="clean")])

    assert stint.started_at == at(0)
    assert [event.kind for event in stint.events] == ["warmed", "spambot_verdict"]


def test_rows_before_any_stint_and_unreadable_starts_belong_to_none() -> None:
    events = [
        row("spambot_verdict", 1, stint=None, status="clean"),
        row("warmed", 2, stint=None),
    ]
    broken = row("warming_started", 3)
    broken = broken.model_copy(update={"stint_started_at": "not a date"})

    assert build_stints([*events, broken]) == []


def test_a_start_without_a_stamp_opens_at_its_own_moment() -> None:
    start = row("warming_started", 5, stint=None)

    [stint] = build_stints([start])

    assert stint.started_at == at(5)


def test_accounts_are_kept_apart() -> None:
    stints = build_stints([*released_life(account="a"), *released_life(account="b")])

    assert sorted(stint.account_id for stint in stints) == ["a", "b"]


def test_parse_moment_reads_naive_as_utc_and_refuses_garbage() -> None:
    assert parse_moment(iso(0).replace("+00:00", "")) == at(0)
    assert parse_moment("garbage") is None
    assert parse_moment(None) is None
