"""The re-key service and its journal: the row stored from the intent, leftovers settled first."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.repositories.account_key_intents import advance_intent, fetch_intent, open_intent
from core.repositories.account_rekeys import fetch_rekey
from schemas.session_rekey import RekeyResult
from services.accounts import session_rekey as svc
from tests.services.accounts import test_session_rekey as base
from tests.session_files import write_session_file

if TYPE_CHECKING:
    from tests.services.accounts.test_session_rekey import _Wire

# The re-key policy tests' fake Telegram (account, gateway, close), reused as is.
wire = base.wire


@pytest.mark.asyncio
async def test_a_rekey_stores_its_row_from_the_intent_and_closes_it(
    wire: _Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    intent_id = await open_intent(
        account_id="acc-1", kind="rekey", device_type="mobile", session_name="sess-1_rekey"
    )
    await advance_intent(intent_id, stage="swapped", new_hash="222", old_hash="111")

    async def _rekey(*_: object) -> RekeyResult:
        return RekeyResult(old_hash="111", new_hash="222", intent_id=intent_id)

    monkeypatch.setattr(svc, "rekey_session", _rekey)
    monkeypatch.setattr(svc.settings.session_audit, "auto_close_old_key", False)

    view = await svc.rekey_account_session("acc-1")

    stored = await fetch_rekey("acc-1")
    assert stored is not None
    assert (stored.old_hash, stored.close_after) == ("111", None)
    assert view.rekeyed_at is not None
    intent = await fetch_intent(intent_id)
    assert intent is not None
    assert intent.outcome == "done"
    assert wire.closes == []


@pytest.mark.asyncio
async def test_a_legacy_swap_that_had_landed_starts_no_new_rekey(wire: _Wire) -> None:
    folder = settings.telegram.session_dir
    write_session_file(folder / "sess-1.session", key=b"new-key")
    write_session_file(folder / "sess-1_rekey.session", key=b"new-key")

    view = await svc.rekey_account_session("acc-1")

    # The old key is recorded as open for the operator; nothing was re-keyed over it.
    assert wire.rekeys == []
    assert view.rekeyed_at is not None
    assert view.closed_at is None
    stored = await fetch_rekey("acc-1")
    assert stored is not None
    assert stored.old_hash == "0"
    assert not (folder / "sess-1_rekey.session").exists()


@pytest.mark.asyncio
async def test_the_leftover_check_runs_after_the_repair_and_before_the_gateway(
    wire: _Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []

    async def _repair(account_id: str) -> int:
        order.append(f"repair:{account_id}")
        return 0

    async def _settle(_account: object, scratch: str) -> bool:
        order.append(f"settle:{scratch}")
        return False

    monkeypatch.setattr(svc, "repair_key_intents", _repair)
    monkeypatch.setattr(svc, "settle_legacy_leftover", _settle)

    await svc.rekey_account_session("acc-1")

    assert order == ["repair:acc-1", "settle:sess-1_rekey"]
    assert [r[0] for r in wire.rekeys] == ["acc-1"]
