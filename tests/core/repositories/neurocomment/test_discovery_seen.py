"""The fleet-wide "already shown" set behind ``hide_seen`` (migration #60)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.db import _get_engine
from core.repositories.neurocomment import list_seen, mark_seen

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_T1 = datetime(2026, 1, 2, tzinfo=UTC)


def _stamps(channel: str) -> tuple[str, str]:
    with _get_engine().connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT first_seen_at, last_seen_at FROM neurocomment_discovery_seen WHERE channel = ?",
            (channel,),
        ).one()
    return str(row[0]), str(row[1])


@pytest.mark.asyncio
async def test_list_seen_returns_only_the_known_subset() -> None:
    await mark_seen(["alpha", "beta"], _T0)

    assert await list_seen(["alpha", "gamma"]) == {"alpha"}
    assert await list_seen([]) == set()
    assert await list_seen(["gamma"]) == set()


@pytest.mark.asyncio
async def test_mark_seen_again_moves_last_seen_and_keeps_first_seen() -> None:
    await mark_seen(["alpha"], _T0)
    await mark_seen(["alpha", "alpha"], _T1)

    assert _stamps("alpha") == (_T0.isoformat(), _T1.isoformat())


@pytest.mark.asyncio
async def test_seen_is_keyed_case_folded_and_private_refs_verbatim() -> None:
    """``News`` and ``news`` are one Telegram peer; an ``id:`` ref has no case to fold."""
    await mark_seen(["News", "id:42"], _T0)

    assert await list_seen(["news", "NEWS", "@News", "id:42"]) == {"news", "id:42"}
    assert _stamps("news")[0] == _T0.isoformat()


@pytest.mark.asyncio
async def test_mark_seen_with_nothing_is_a_noop() -> None:
    await mark_seen([], _T0)

    assert await list_seen(["alpha"]) == set()
