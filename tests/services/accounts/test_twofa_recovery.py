"""Every state a killed, refused or lost 2FA write can leave — none of them terminal.

Its own module because ``test_twofa.py`` carries the happy paths and is at the
700-line test-source cap. What lives here is one shape of bug: an outcome in
which the dashboard ends up holding a password Telegram does not have, or
holding none while Telegram has one, and the card then offers change / remove /
attach-email — all of which answer ``twofa_current_password_invalid`` forever,
because ``has_stored_password`` is ``True`` so the not-stored precondition can
never fire again. Each test below drives one such outcome to the state that is
still recoverable.
"""

from __future__ import annotations

import pytest

from core.db import create_account, fetch_account_twofa_password
from schemas.accounts import AccountCreate
from schemas.telegram_actions import ActionResult
from schemas.twofa import AccountTwoFactorUpdateRequest
from services.accounts import AccountActionError, set_account_twofa
from tests.services.accounts._twofa_support import (
    STORED,
    account_with_password,
    ok_result,
    patch_log,
    patch_lost_answer,
    patch_read,
    status,
)


@pytest.mark.asyncio
async def test_a_lost_answer_over_a_stale_stored_password_keeps_the_new_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stored value is stale, so "keep the previous one" throws away the only copy.

    State: something stored, and the live read says ``has_password=False`` — the
    operator removed 2FA from the phone, or an earlier clear failed. The stored
    password is stale BY DEFINITION there, so a lost answer must not keep it in
    preference to the password Telegram may now require. Keeping it persists nothing
    at all: the new password lands in no row, and the next attempt reads
    ``has_password=True`` with a stale value stored, so change / re-submit / remove /
    attach-email all answer ``twofa_current_password_invalid`` permanently — neither
    the not-stored guard nor the stale branch can fire.

    ``previous_kept`` is also what the card's copy is derived from, and there it would
    be a lie: Telegram had no password, so "the previous one" cannot be in force.
    """
    await account_with_password("acc-stale-lost")
    patch_read(monkeypatch, status(has_password=False))
    events = patch_log(monkeypatch)
    patch_lost_answer(monkeypatch)

    created = await set_account_twofa("acc-stale-lost", AccountTwoFactorUpdateRequest())

    assert created.confirmed is False
    assert created.previous_kept is False
    assert created.stored is True
    # The load-bearing assertion: the column holds the password Telegram may now
    # require, not the one it provably does not.
    assert await fetch_account_twofa_password("acc-stale-lost") == created.password
    assert created.password != STORED
    assert events[0][2]["confirmed"] is False


def _patch_execute_watching_the_column(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Answer ``ok``, recording what the column held AT THE MOMENT of the RPC."""
    seen: list[str | None] = []

    async def _capture(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        seen.append(await fetch_account_twofa_password(account_id))
        return ok_result(account_id)

    monkeypatch.setattr("services.accounts.twofa.execute", _capture)
    return seen


@pytest.mark.asyncio
async def test_a_fresh_set_persists_the_candidate_before_the_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one branch here that is SILENT if the process dies, so it is ordered first.

    Between "Telegram applied the password" and the persist there are several awaits
    — ``execute`` itself awaits ``log_event``, a threaded SQLite insert plus an SSE
    fan-out. A process death in that window leaves the password live on Telegram,
    nothing in the row and no log line: the operator never saw the plaintext, and
    nothing can recover it. On a fresh set there is nothing to lose by writing first,
    and the opposite failure — stored but never applied — is recoverable: it reads as
    ``has_stored_password=True`` with ``has_password=False``, which the stale branch
    and the card's "forget the stale password" row already handle.
    """
    await create_account(AccountCreate(account_id="acc-order"))
    patch_read(monkeypatch, status())
    patch_log(monkeypatch)
    seen = _patch_execute_watching_the_column(monkeypatch)

    created = await set_account_twofa("acc-order", AccountTwoFactorUpdateRequest())

    assert seen == [created.password]
    assert created.stored is True
    assert await fetch_account_twofa_password("acc-order") == created.password


@pytest.mark.asyncio
async def test_a_change_still_persists_only_after_telegram_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The asymmetry the sibling above depends on, from the other side.

    On a CHANGE the stored value is a credential Telegram is known to accept, so
    writing the candidate over it first would trade a recoverable ambiguity for the
    unrecoverable loss the lost-answer branch exists to avoid.
    """
    await account_with_password("acc-order-change")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    seen = _patch_execute_watching_the_column(monkeypatch)

    created = await set_account_twofa(
        "acc-order-change",
        AccountTwoFactorUpdateRequest(password="the-new-one"),
    )

    assert seen == [STORED]
    assert created.stored is True
    assert await fetch_account_twofa_password("acc-order-change") == "the-new-one"


@pytest.mark.asyncio
async def test_a_refused_fresh_set_leaves_no_password_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ANSWERED refusal proves Telegram did not apply it, so the pre-write is undone.

    Without the rollback the pre-write becomes its own lockout: the column would hold
    a password Telegram never had, ``twofa_password_not_stored`` could no longer fire
    (something IS stored), and every later change or removal would authorise itself
    with a value Telegram rejects. Reachable whenever the live read failed and
    Telegram in fact has a password — the one case the not-stored guard cannot see.
    """
    await create_account(AccountCreate(account_id="acc-refused"))
    patch_read(monkeypatch, status())
    patch_log(monkeypatch)

    async def _refuse(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        return ActionResult(
            status="failed",
            action_type="set_twofa_password",
            account_id=account_id,
            error_type="TwoFactorGatewayError",
            error_message="twofa_current_password_invalid",
        )

    monkeypatch.setattr("services.accounts.twofa.execute", _refuse)

    with pytest.raises(AccountActionError) as excinfo:
        await set_account_twofa("acc-refused", AccountTwoFactorUpdateRequest())

    assert excinfo.value.code == "twofa_current_password_invalid"
    assert await fetch_account_twofa_password("acc-refused") is None
