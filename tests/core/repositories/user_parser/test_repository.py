"""User-parser repository: runs settle with their people, bases list/rename/delete, presets."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import configure_database
from core.repositories import user_parser as repository
from schemas.user_parser import UserParserRequest, UserParserSettings
from schemas.user_parser_run import UserParserRun, UserParserSourceReport, UserParserUser

if TYPE_CHECKING:
    from pathlib import Path

    from schemas.user_parser_run import UserParserRunStatus


@pytest.fixture(autouse=True)
def isolate_user_parser_db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


def _request() -> UserParserRequest:
    return UserParserRequest(mode="members", sources=["@a", "@b"], account_ids=["acc"])


async def _open(run_id: str, created_at: str = "2026-10-01T00:00:00+00:00") -> UserParserRun:
    run = UserParserRun(
        run_id=run_id,
        name=f"Base {run_id}",
        mode="members",
        status="running",
        created_at=created_at,
        sources_total=2,
        sources=[UserParserSourceReport(source="@a"), UserParserSourceReport(source="@b")],
    )
    await repository.create_run(run, _request())
    return run


def _user(user_id: int, **fields: object) -> UserParserUser:
    return UserParserUser.model_validate({"user_id": user_id, **fields})


async def _settle(
    run: UserParserRun, users: list[UserParserUser], status: UserParserRunStatus = "done"
) -> UserParserRun:
    settled = run.model_copy(
        update={
            "status": status,
            "finished_at": "2026-10-01T01:00:00+00:00",
            "kept": len(users),
            "collected_raw": len(users) + 3,
            "sources_done": 2,
            "filtered": {"bot": 3},
            "sources": [
                UserParserSourceReport(source="@a", status="ok", count=4),
                UserParserSourceReport(source="@b", status="hidden"),
            ],
        }
    )
    await repository.settle_run(settled, users)
    return settled


@pytest.mark.asyncio
async def test_a_settled_run_is_read_back_whole() -> None:
    run = await _open("r1")
    assert (await repository.fetch_run("r1")).status == "running"  # ty: ignore[unresolved-attribute]

    await _settle(run, [_user(1, first_name="Аня", sources=["@a"]), _user(2)])

    stored = await repository.fetch_run("r1")
    assert stored is not None
    assert (stored.status, stored.kept, stored.collected_raw) == ("done", 2, 5)
    assert stored.filtered == {"bot": 3}
    assert [(s.source, s.status, s.count) for s in stored.sources] == [
        ("@a", "ok", 4),
        ("@b", "hidden", 0),
    ]
    assert await repository.fetch_run("missing") is None


@pytest.mark.asyncio
async def test_bases_list_settled_runs_newest_first_and_rename() -> None:
    old = await _open("old", "2026-10-01T00:00:00+00:00")
    new = await _open("new", "2026-10-02T00:00:00+00:00")
    await _open("running", "2026-10-03T00:00:00+00:00")
    await _settle(old, [_user(1)])
    await _settle(new, [], status="stopped")

    bases = (await repository.list_bases()).items
    assert [(b.run_id, b.status, b.kept) for b in bases] == [
        ("new", "stopped", 0),
        ("old", "done", 1),
    ]
    assert bases[0].sources == ["@a", "@b"]

    renamed = await repository.rename_base("old", "Крипта")
    assert renamed is not None
    assert renamed.name == "Крипта"
    assert await repository.rename_base("missing", "x") is None


@pytest.mark.asyncio
async def test_deleting_a_base_takes_its_people_and_refuses_a_running_run() -> None:
    run = await _open("r1")
    other = await _open("r2")
    await _settle(run, [_user(1), _user(2)])
    await _settle(other, [_user(2)])
    await _open("live")

    assert await repository.delete_base("live") == "running"
    assert await repository.delete_base("missing") == "not_found"
    assert await repository.delete_base("r1") is None

    assert await repository.fetch_run("r1") is None
    assert (await repository.page_users("r1", search="", offset=0, limit=10)).total == 0
    assert await repository.collected_user_ids([1, 2], exclude_run_id="x") == {2}


@pytest.mark.asyncio
async def test_people_page_most_active_first_and_search_folds_cyrillic() -> None:
    run = await _open("r1")
    await _settle(
        run,
        [
            _user(10, first_name="Анна", message_count=2),
            _user(11, first_name="Борис", username="boris_100", message_count=9),
            _user(12, first_name="x", last_name="анНа", message_count=5),
            _user(13, first_name="50%", message_count=1),
        ],
    )

    page = await repository.page_users("r1", search="", offset=0, limit=2)
    assert ([u.user_id for u in page.items], page.total) == ([11, 12], 4)
    second = await repository.page_users("r1", search="", offset=2, limit=2)
    assert [u.user_id for u in second.items] == [10, 13]

    found = await repository.page_users("r1", search="  АННА ", offset=0, limit=10)
    assert sorted(u.user_id for u in found.items) == [10, 12]
    assert [
        u.user_id
        for u in (await repository.page_users("r1", search="BORIS", offset=0, limit=10)).items
    ] == [11]
    assert [
        u.user_id
        for u in (await repository.page_users("r1", search="12", offset=0, limit=10)).items
    ] == [12]
    # LIKE wildcards in the search are literal.
    percent = await repository.page_users("r1", search="%", offset=0, limit=10)
    assert [u.user_id for u in percent.items] == [13]
    assert (await repository.page_users("r1", search="_", offset=0, limit=10)).total == 1


@pytest.mark.asyncio
async def test_a_person_round_trips_with_flags_and_sources() -> None:
    run = await _open("r1")
    person = _user(
        7,
        username="nick",
        is_premium=True,
        has_photo=True,
        last_seen="week",
        message_count=3,
        first_at="2026-09-01T00:00:00+00:00",
        last_at="2026-09-03T00:00:00+00:00",
        sources=["@a", "@b"],
    )
    await _settle(run, [person])

    (stored,) = (await repository.page_users("r1", search="", offset=0, limit=5)).items
    assert stored == person


@pytest.mark.asyncio
async def test_the_export_cursor_walks_the_base_in_id_order() -> None:
    run = await _open("r1")
    await _settle(run, [_user(user_id) for user_id in (5, 3, 9, 1)])

    first = await repository.users_after("r1", None, 3)
    rest = await repository.users_after("r1", first[-1].user_id, 3)

    assert [u.user_id for u in first] == [1, 3, 5]
    assert [u.user_id for u in rest] == [9]


@pytest.mark.asyncio
async def test_earlier_finds_exclude_the_asking_run_and_survive_large_lists() -> None:
    first = await _open("r1")
    second = await _open("r2")
    await _settle(first, [_user(user_id) for user_id in range(1, 2001)])
    await _settle(second, [_user(1), _user(5000)])

    found = await repository.collected_user_ids(range(1, 5001), exclude_run_id="r2")

    assert len(found) == 2000
    assert 5000 not in found
    assert await repository.collected_user_ids([], exclude_run_id="r1") == set()


@pytest.mark.asyncio
async def test_a_restart_marks_running_runs_interrupted_only() -> None:
    done = await _open("done")
    await _settle(done, [])
    await _open("live")

    assert await repository.interrupt_running("2026-10-05T00:00:00+00:00") == 1

    live = await repository.fetch_run("live")
    assert live is not None
    assert (live.status, live.stop_reason, live.finished_at) == (
        "interrupted",
        "restart",
        "2026-10-05T00:00:00+00:00",
    )
    assert (await repository.fetch_run("done")).status == "done"  # ty: ignore[unresolved-attribute]


@pytest.mark.asyncio
async def test_presets_are_unique_ignoring_case_and_deletable() -> None:
    values = UserParserSettings(mode="messages", keywords=["shop"])
    created = await repository.create_preset("Крипта", values)
    assert not isinstance(created, str)

    assert await repository.create_preset("КРИПТА", UserParserSettings()) == "name_taken"
    listed = (await repository.list_presets()).items
    assert [(p.name, p.settings) for p in listed] == [("Крипта", values)]

    assert await repository.delete_preset(created.preset_id) is True
    assert await repository.delete_preset(created.preset_id) is False
    assert (await repository.list_presets()).items == []
