"""The join cap is counted on the engine's clock, like everything else the scheduler reads."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.repositories.neurocomment import record_join
from services.chat_broadcast import _join
from tests.services.chat_broadcast.fakes import seed

if TYPE_CHECKING:
    from tests.services.chat_broadcast.fakes import Clock

_DAY = 86_400


@pytest.mark.asyncio
async def test_a_join_leaves_the_window_when_the_engines_clock_says_so(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.chat_broadcast, "max_joins_per_account_per_day", 1)
    await seed(accounts=("a1",))
    await record_join("a1")

    assert await _join.at_join_cap("a1")
    clock.advance(_DAY + 60)

    # The join was a day and a minute ago on the run's clock, whatever the wall clock says.
    assert not await _join.at_join_cap("a1")
