"""Each domain's Telegram seam stamps its own prefix on the gateway's event names.

The seam is the single place the domain is chosen — call sites pass nothing — so an
unbound re-export would silently put warming's and neurocomment's gateway rows back
under the shared, unfiltered ``telegram_*`` name. Asserted through a real ``execute``
call, so a refactor away from ``functools.partial`` passes on behaviour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

# Import order matters, not just style: ``services.neurocomment._seams`` cannot be the
# first module imported — on its own it trips the pre-existing ``core.db`` <->
# ``core.repositories.neurocomment`` cycle (``ImportError: ChannelAlreadyAssignedError``,
# reproducible with ``python -c "import services.neurocomment._seams"``). Importing
# ``core.telegram_client`` first pulls ``core.db`` in cleanly and breaks the cycle.
from core.telegram_client import _actions
from schemas.telegram_actions import SetOnline
from services.neurocomment import _seams as neurocomment_seams
from services.warming import _seams as warming_seams
from tests.core.telegram_client.helpers import patch_action_client

if TYPE_CHECKING:
    from types import ModuleType


class _OnlineClient:
    async def connect(self) -> None:
        return None

    async def __call__(self, _request: object) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("seam", "expected"),
    [
        pytest.param(warming_seams, "warming_telegram_set_online", id="warming"),
        pytest.param(neurocomment_seams, "neurocomment_telegram_set_online", id="neurocomment"),
    ],
)
async def test_domain_seam_stamps_its_prefix_on_the_gateway_event(
    monkeypatch: pytest.MonkeyPatch,
    seam: ModuleType,
    expected: str,
) -> None:
    events: list[str] = []

    async def _fake_log(_level: str, event: str, **_kwargs: object) -> None:
        events.append(event)

    patch_action_client(monkeypatch, _OnlineClient())
    monkeypatch.setattr(_actions, "log_event", _fake_log)

    await seam.execute("acc-seam", SetOnline(online=True))

    assert events == [expected]
