"""User-parser runs: open one, settle it with its people, list, rename and delete bases."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Literal, cast

from sqlalchemy import delete, insert, select, update

from core.db import _get_engine
from core.repositories.user_parser._tables import _user_parser_runs as _runs
from core.repositories.user_parser._tables import _user_parser_users as _users
from schemas.user_parser_run import (
    UserParserBase,
    UserParserBaseList,
    UserParserRun,
    UserParserSourceReport,
)

if TYPE_CHECKING:
    from sqlalchemy import RowMapping

    from schemas.user_parser import ParserMode, UserParserRequest
    from schemas.user_parser_run import UserParserRunStatus, UserParserUser

# Why a delete did not happen: the base is unknown, or its run is still collecting.
DeleteMiss = Literal["not_found", "running"]


def _sources(row: RowMapping) -> list[UserParserSourceReport]:
    return [UserParserSourceReport.model_validate(item) for item in json.loads(row["sources_json"])]


def _run(row: RowMapping) -> UserParserRun:
    return UserParserRun(
        run_id=str(row["run_id"]),
        name=str(row["name"]),
        mode=cast("ParserMode", row["mode"]),
        status=cast("UserParserRunStatus", row["status"]),
        created_at=str(row["created_at"]),
        finished_at=row["finished_at"],
        stop_reason=row["stop_reason"],
        sources_total=int(row["sources_total"]),
        sources_done=int(row["sources_done"]),
        collected_raw=int(row["collected_raw"]),
        kept=int(row["kept"]),
        sources=_sources(row),
        filtered={str(k): int(v) for k, v in json.loads(row["filtered_json"]).items()},
    )


def _base(row: RowMapping) -> UserParserBase:
    return UserParserBase(
        run_id=str(row["run_id"]),
        name=str(row["name"]),
        mode=cast("ParserMode", row["mode"]),
        status=cast("UserParserRunStatus", row["status"]),
        created_at=str(row["created_at"]),
        sources=[report.source for report in _sources(row)],
        kept=int(row["kept"]),
    )


def _create_run(run: UserParserRun, request: UserParserRequest) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            insert(_runs).values(
                run_id=run.run_id,
                name=run.name,
                created_at=run.created_at,
                status=run.status,
                mode=run.mode,
                request_json=request.model_dump_json(),
                account_ids_json=json.dumps(request.account_ids),
                sources_total=run.sources_total,
                sources_json=json.dumps([s.model_dump() for s in run.sources]),
            )
        )


async def create_run(run: UserParserRun, request: UserParserRequest) -> None:
    """Write the ``running`` row a start opens; the live progress stays in memory."""
    await asyncio.to_thread(_create_run, run, request)


def _search_key(user: UserParserUser) -> str:
    return " ".join(
        part for part in (user.first_name, user.last_name, user.username or "", str(user.user_id))
    ).casefold()


def _user_row(run_id: str, user: UserParserUser) -> dict[str, object]:
    row = user.model_dump(exclude={"sources"})
    return row | {
        "run_id": run_id,
        "sources_json": json.dumps(user.sources, ensure_ascii=False),
        "search_key": _search_key(user),
    }


def _settle_run(run: UserParserRun, users: list[UserParserUser]) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_runs)
            .where(_runs.c.run_id == run.run_id)
            .values(
                status=run.status,
                finished_at=run.finished_at,
                stop_reason=run.stop_reason,
                sources_total=run.sources_total,
                sources_done=run.sources_done,
                collected_raw=run.collected_raw,
                kept=run.kept,
                sources_json=json.dumps([s.model_dump() for s in run.sources], ensure_ascii=False),
                filtered_json=json.dumps(run.filtered),
            )
        )
        if users:
            connection.execute(insert(_users), [_user_row(run.run_id, user) for user in users])


async def settle_run(run: UserParserRun, users: list[UserParserUser]) -> None:
    """Close a run and store the people it kept, in one transaction."""
    await asyncio.to_thread(_settle_run, run, users)


def _fetch_run(run_id: str) -> UserParserRun | None:
    with _get_engine().connect() as connection:
        row = connection.execute(select(_runs).where(_runs.c.run_id == run_id)).mappings().first()
    return None if row is None else _run(row)


async def fetch_run(run_id: str) -> UserParserRun | None:
    return await asyncio.to_thread(_fetch_run, run_id)


def _list_bases() -> UserParserBaseList:
    statement = (
        select(_runs)
        .where(_runs.c.status != "running")
        .order_by(_runs.c.created_at.desc(), _runs.c.run_id.desc())
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return UserParserBaseList(items=[_base(row) for row in rows])


async def list_bases() -> UserParserBaseList:
    """Every settled run, newest first. A run still collecting is not a base yet."""
    return await asyncio.to_thread(_list_bases)


def _rename_base(run_id: str, name: str) -> UserParserBase | None:
    with _get_engine().begin() as connection:
        changed = connection.execute(
            update(_runs).where(_runs.c.run_id == run_id).values(name=name)
        ).rowcount
        if not changed:
            return None
        row = connection.execute(select(_runs).where(_runs.c.run_id == run_id)).mappings().one()
    return _base(row)


async def rename_base(run_id: str, name: str) -> UserParserBase | None:
    return await asyncio.to_thread(_rename_base, run_id, name)


def _delete_base(run_id: str) -> DeleteMiss | None:
    with _get_engine().begin() as connection:
        status = connection.execute(
            select(_runs.c.status).where(_runs.c.run_id == run_id)
        ).scalar_one_or_none()
        if status is None:
            return "not_found"
        if status == "running":
            return "running"
        # The people go with it (``ON DELETE CASCADE``).
        connection.execute(delete(_runs).where(_runs.c.run_id == run_id))
    return None


async def delete_base(run_id: str) -> DeleteMiss | None:
    """Delete a base and its people for good; ``None`` once it is gone."""
    return await asyncio.to_thread(_delete_base, run_id)


def _interrupt_running(finished_at: str) -> int:
    with _get_engine().begin() as connection:
        return connection.execute(
            update(_runs)
            .where(_runs.c.status == "running")
            .values(status="interrupted", finished_at=finished_at, stop_reason="restart")
        ).rowcount


async def interrupt_running(finished_at: str) -> int:
    """Mark every run a dead process left ``running`` as ``interrupted``; how many."""
    return await asyncio.to_thread(_interrupt_running, finished_at)
