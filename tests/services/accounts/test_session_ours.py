"""The operator marks a listed session as ours (and can take the mark back); nothing is closed."""

from __future__ import annotations

import pytest

from core.repositories.account_own_authorizations import fetch_own_authorizations
from core.repositories.session_audits import fetch_session_audit, save_session_audit
from schemas.accounts import AccountCreate
from schemas.session_audit import AccountSession, AccountSessionAudit, AuthorizationList
from services.accounts import AccountActionError, add_account, mark_session_ours


def _session(hash_: str, *, current: bool = False, ours: bool = False) -> AccountSession:
    return AccountSession(
        hash=hash_,
        is_current=current,
        is_ours=ours,
        official_app=False,
        device_model="Mac Studio",
        platform="Desktop",
        system_version="macOS 15.0",
        app_name="userbot01",
        ip="192.0.2.10",
        country="Nowhere",
    )


async def _audited() -> None:
    await add_account(AccountCreate(account_id="acc-1"))
    sessions = [
        _session("0", current=True, ours=True),
        _session("111"),  # an orphan of a failed sign-in: ours, Telebuba cannot prove it
        _session("222"),  # a stranger
    ]
    await save_session_audit(
        AccountSessionAudit.of(
            "acc-1", "2026-11-16T10:00:00+00:00", AuthorizationList(sessions=sessions)
        )
    )


@pytest.mark.asyncio
async def test_a_marked_session_is_ours_and_the_mark_can_be_taken_back() -> None:
    await _audited()

    marked = await mark_session_ours("acc-1", "111", ours=True)

    assert marked.foreign_count == 1
    [orphan] = [s for s in marked.sessions if s.hash == "111"]
    assert (orphan.is_ours, orphan.kind) == (True, "manual")
    assert await fetch_own_authorizations("acc-1") == {"111": "manual"}

    unmarked = await mark_session_ours("acc-1", "111", ours=False)

    assert unmarked.foreign_count == 2
    assert await fetch_own_authorizations("acc-1") == {}
    saved = await fetch_session_audit("acc-1")
    assert saved is not None
    assert saved.foreign_count == 2


@pytest.mark.parametrize("session_hash", ["0", "999"])
@pytest.mark.asyncio
async def test_the_asking_session_or_one_not_listed_is_refused(session_hash: str) -> None:
    await _audited()

    with pytest.raises(AccountActionError) as refused:
        await mark_session_ours("acc-1", session_hash, ours=True)

    assert refused.value.code == "session_not_listed"
    assert await fetch_own_authorizations("acc-1") == {}
