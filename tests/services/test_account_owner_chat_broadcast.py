"""A chat-broadcast hold keeps warming, the listener and account removal off the session.

The registry gained a third writer; every reader that refused a neuroshilling hold has to
refuse this one too, each with a code that names who holds the account.
"""

from __future__ import annotations

import pytest

from services import _account_owner
from services.neurocomment import _runtime as nc_runtime
from services.neurocomment import _runtime_operations as nc_operations
from services.warming import _exclusion


@pytest.mark.parametrize(
    ("owner", "code"),
    [
        ("neuroshilling", "account_busy_neuroshilling"),
        ("chat_broadcast", "account_busy_chat_broadcast"),
    ],
)
def test_warming_refuses_an_account_a_campaign_holds(
    owner: _account_owner.Owner, code: str
) -> None:
    _account_owner.try_claim("acc", owner, "holder-1")

    with pytest.raises(_exclusion.AccountUnavailableError) as refused:
        _exclusion.assert_not_campaign_held("acc")

    assert refused.value.code == code


def test_warming_does_not_refuse_its_own_hold() -> None:
    _account_owner.try_claim("acc", "warming", "run-1")

    _exclusion.assert_not_campaign_held("acc")


@pytest.mark.asyncio
async def test_the_listener_refuses_an_account_a_broadcast_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no_warming() -> list[str]:
        return []

    monkeypatch.setattr(nc_runtime, "_list_warming_account_ids", _no_warming)
    _account_owner.try_claim("acc", "chat_broadcast", "campaign-1")

    with pytest.raises(nc_operations.ListenerBusyChatBroadcastError):
        await nc_operations._refuse_if_busy("acc")
