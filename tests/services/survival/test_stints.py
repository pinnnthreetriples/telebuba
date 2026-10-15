"""Journal rows → lives and waves: lives cut at deletions, waves opened by new starts only."""

from __future__ import annotations

from services.survival._stints import build_lives, parse_moment
from tests.services.survival._support import at, iso, limited, released_life, row


def test_a_restart_with_the_same_start_opens_no_second_wave() -> None:
    events = [
        *released_life(14),
        row("warming_started", 20, stint=0, new_stint=False),
        row("promoted", 25, stint=0),
    ]

    [life] = build_lives(events)

    assert [wave.wave for wave in life.waves] == [1]
    assert life.started_at == at(0)
    assert len(life.events) == len(events)
    assert life.current


def test_a_fresh_start_is_the_next_wave_of_the_same_life() -> None:
    events = [*released_life(14), row("warming_started", 30, stint=30, new_stint=True)]

    [life] = build_lives(events)

    first, second = life.waves
    assert (first.wave, second.wave) == (1, 2)
    assert second.started_at == at(30)
    assert first.events[-1].kind == "warmed"
    assert life.started_at == at(0)


def test_the_recorded_wave_number_wins_and_old_rows_count_in_order() -> None:
    events = [
        row("warming_started", 0),
        row("warming_started", 10, stint=10, wave=3),
        row("warming_started", 20, stint=20),
        # A recorded number that would go backwards is not trusted.
        row("warming_started", 30, stint=30, wave=2),
    ]

    [life] = build_lives(events)

    assert [wave.wave for wave in life.waves] == [1, 3, 4, 5]


def test_a_repeated_id_after_a_deletion_is_a_new_life() -> None:
    # The first account was deleted; a re-import under the same id warms again.
    events = [
        *released_life(14),
        limited(16),
        row("deleted", 17, reason="retired"),
        row("warming_started", 40, stint=40, new_stint=True),
    ]

    old, new = build_lives(events)

    assert not old.current
    assert old.events[-1].kind == "deleted"
    assert new.current
    assert [wave.wave for wave in new.waves] == [1]
    assert all(event.kind != "spambot_verdict" for event in new.all_events)


def test_a_row_naming_an_unopened_stint_opens_it() -> None:
    # The start fell out of the log retention before the journal existed.
    [life] = build_lives([row("warmed", 14, stint=0), row("spambot_verdict", 15, status="clean")])

    assert life.started_at == at(0)
    assert [event.kind for event in life.events] == ["warmed", "spambot_verdict"]


def test_rows_before_any_wave_and_unreadable_starts_make_no_life() -> None:
    events = [
        row("spambot_verdict", 1, stint=None, status="clean"),
        row("warmed", 2, stint=None),
    ]
    broken = row("warming_started", 3)
    broken = broken.model_copy(update={"stint_started_at": "not a date"})

    assert build_lives([*events, broken]) == []


def test_rows_before_the_first_wave_stay_out_of_the_life_events() -> None:
    early = row("session_dead", 1, stint=None, status="unauthorized")

    [life] = build_lives([early, row("warming_started", 5, stint=5)])

    assert early not in life.events
    assert early in life.all_events


def test_history_rebuilt_from_the_logs_is_one_wave() -> None:
    # Migration 74 stamped every logged start (restarts too) with its own moment.
    rebuilt = [
        row("warming_started", 0, stint=0).model_copy(update={"backfilled": True}),
        row("warming_started", 3, stint=3).model_copy(update={"backfilled": True}),
        row("warmed", 14, stint=3).model_copy(update={"backfilled": True}),
    ]
    live = row("warming_started", 20, stint=20, wave=2)

    [life] = build_lives([*rebuilt, live])

    assert [wave.wave for wave in life.waves] == [1, 2]


def test_rows_before_the_live_account_row_are_an_earlier_life() -> None:
    # The old life's ``deleted`` row never landed (it is best-effort).
    events = [*released_life(14), row("warming_started", 40, stint=40, wave=1)]

    old, new = build_lives(events, {"acc-1": iso(30)})

    assert [wave.wave for wave in old.waves] == [1]
    assert [wave.wave for wave in new.waves] == [1]
    assert new.started_at == at(40)


def test_a_start_without_a_stamp_opens_at_its_own_moment() -> None:
    start = row("warming_started", 5, stint=None)

    [life] = build_lives([start])

    assert life.started_at == at(5)


def test_accounts_are_kept_apart() -> None:
    lives = build_lives([*released_life(account="a"), *released_life(account="b")])

    assert sorted(life.account_id for life in lives) == ["a", "b"]


def test_parse_moment_reads_naive_as_utc_and_refuses_garbage() -> None:
    assert parse_moment(iso(0).replace("+00:00", "")) == at(0)
    assert parse_moment("garbage") is None
    assert parse_moment(None) is None
