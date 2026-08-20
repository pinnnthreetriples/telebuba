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

from typing import TYPE_CHECKING

import pytest

from core.db import (
    create_account,
    fetch_account_twofa_password,
    set_account_twofa_password,
)
from core.telegram_client import TelegramReadError
from schemas.accounts import AccountCreate
from schemas.telegram_actions import ActionResult
from schemas.twofa import AccountTwoFactorUpdateRequest
from services.accounts import (
    AccountActionError,
    remove_account_twofa,
    set_account_twofa,
)
from tests.services.accounts._twofa_support import (
    STORED,
    account_with_password,
    ok_result,
    patch_log,
    patch_lost_answer,
    patch_read,
    status,
)

if TYPE_CHECKING:
    from schemas.telegram_actions_twofa import TwoFactorStatusResult


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


@pytest.mark.asyncio
async def test_an_email_unconfirmed_change_keeps_the_password_known_to_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C2: the flag the gateway sends MUST turn into ``confirmed=False`` here.

    The single weakest seam the mutation campaign found: ``confirmed = not lost and
    not result.twofa_email_unconfirmed`` mutated to ``confirmed = not lost`` survived
    all 137 tests, because no service-level test ever set that flag on the password
    path. With the mutant alive, a CHANGE whose outcome Telegram called
    ``EMAIL_UNCONFIRMED`` is treated as a clean success: the candidate is persisted
    over a credential Telegram is known to accept, and if the write was in fact held
    the column now holds a password nothing will authorise — the exact unrecoverable
    loss the whole asymmetry exists to prevent.
    """
    await account_with_password("acc-unconfirmed-change")
    patch_read(monkeypatch, status(has_password=True))
    events = patch_log(monkeypatch)

    async def _unconfirmed(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        return ok_result(account_id, twofa_email_unconfirmed=True)

    monkeypatch.setattr("services.accounts.twofa.execute", _unconfirmed)

    created = await set_account_twofa(
        "acc-unconfirmed-change",
        AccountTwoFactorUpdateRequest(password="the-new-one"),
    )

    assert created.confirmed is False
    # The load-bearing pair: the known-good value survives and the new one is not
    # persisted over it, exactly as on a lost answer.
    assert await fetch_account_twofa_password("acc-unconfirmed-change") == STORED
    assert created.stored is False
    assert created.previous_kept is True
    assert created.password == "the-new-one"
    assert events[0][2]["confirmed"] is False


@pytest.mark.asyncio
async def test_a_lost_answer_over_a_failed_live_read_reports_unknown_not_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``previous_kept=True`` is a claim about the LIVE state, so a dead read cannot make it.

    ``stale_stored`` is ``False`` when the read answered nothing, which is
    indistinguishable from "Telegram definitely has a password" — so the response
    asserted "one of these two is in force" on the strength of a read that said
    nothing at all. Telegram may hold neither. ``None`` is the third answer.
    """
    await account_with_password("acc-lost-blind")

    async def _boom(account_id: str, action: object) -> TwoFactorStatusResult:  # noqa: ARG001
        reason = "unavailable: TelegramClientPoolError"
        raise TelegramReadError(reason, kind="unavailable")

    monkeypatch.setattr("services.accounts.twofa.execute_read", _boom)
    patch_log(monkeypatch)
    patch_lost_answer(monkeypatch)

    created = await set_account_twofa("acc-lost-blind", AccountTwoFactorUpdateRequest())

    assert created.confirmed is False
    assert created.previous_kept is None
    # The previous value IS still there — that half was never in doubt. What the
    # response may not do is call it "in force".
    assert await fetch_account_twofa_password("acc-lost-blind") == STORED


@pytest.mark.asyncio
async def test_a_rollback_that_failed_says_so_instead_of_a_plain_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H2: the poisoned column has to be reported, or the state is silently terminal.

    A fresh set writes the candidate BEFORE the RPC and rolls it back on an answered
    refusal. ``_remember_password`` swallows every exception and answers ``False``,
    and the rollback caller ignored that — so a locked database (likely exactly then)
    left a password Telegram had just rejected in the column while the operator read
    an ordinary "wrong password". From there nothing recovers: ``has_stored_password``
    is ``True`` so the not-stored guard cannot fire, every verb sends the bogus value,
    and the stale branch cannot help because the live read says ``has_password=True``.
    """
    await create_account(AccountCreate(account_id="acc-poisoned"))
    patch_read(monkeypatch, status())
    patch_log(monkeypatch)
    written: list[str | None] = []

    async def _refuse(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        return ActionResult(
            status="failed",
            action_type="set_twofa_password",
            account_id=account_id,
            error_type="TwoFactorGatewayError",
            error_message="twofa_current_password_invalid",
        )

    async def _write_once_then_fail(account_id: str, password: str | None) -> bool:
        written.append(password)
        if password is None:
            msg = "database is locked"
            raise RuntimeError(msg)
        return await set_account_twofa_password(account_id, password)

    monkeypatch.setattr("services.accounts.twofa.execute", _refuse)
    monkeypatch.setattr(
        "services.accounts.twofa.set_account_twofa_password",
        _write_once_then_fail,
    )

    with pytest.raises(AccountActionError) as excinfo:
        await set_account_twofa("acc-poisoned", AccountTwoFactorUpdateRequest())

    # NOT ``twofa_current_password_invalid``: the operator has to learn the column is
    # poisoned, because the fix is "forget the stored password", not "try again".
    assert excinfo.value.code == "twofa_rollback_failed"
    # The rollback was attempted, and it is the failure that is being reported.
    assert written[-1] is None
    assert await fetch_account_twofa_password("acc-poisoned") is not None


@pytest.mark.asyncio
async def test_forget_only_drops_a_poisoned_column_with_no_rpc_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The way OUT of every one of those states, and it must not need ``stale``.

    Ungating the clear-only branch is what closes the whole family: a failed
    rollback, a process death between a fresh set's pre-write and its RPC, and a lost
    answer over a dead read all end with a stored password Telegram does not accept
    while the live read says ``has_password=True`` — so the automatic ``stale`` branch
    can never fire and the RPC leg authorises itself with the bogus value forever.
    """
    await account_with_password("acc-forget")
    reads = patch_read(monkeypatch, status(has_password=True))
    events = patch_log(monkeypatch)
    actions: list[object] = []

    async def _record(account_id: str, action: object) -> ActionResult:
        actions.append(action)
        return ok_result(account_id)

    monkeypatch.setattr("services.accounts.twofa.execute", _record)

    view = await remove_account_twofa("acc-forget", forget_only=True)

    # No RPC, even though Telegram says it HAS a password: the operator asked to
    # forget this dashboard's copy, not to turn 2FA off.
    assert actions == []
    assert await fetch_account_twofa_password("acc-forget") is None
    assert view.has_stored_password is False
    assert events[0][2] == {"stale": False, "forget_only": True}
    assert reads


@pytest.mark.asyncio
async def test_the_default_removal_still_spends_its_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other side of the ungating: ``forget_only`` must be OPT-IN.

    A clear-only default would turn every "remove 2FA" click into a silent local
    delete, leaving the password live on Telegram with no copy anywhere — the exact
    loss ``forget_only`` exists to escape.
    """
    await account_with_password("acc-not-forget")
    patch_read(monkeypatch, status(has_password=True), status())
    patch_log(monkeypatch)
    actions: list[object] = []

    async def _record(account_id: str, action: object) -> ActionResult:
        actions.append(action)
        return ok_result(account_id)

    monkeypatch.setattr("services.accounts.twofa.execute", _record)

    await remove_account_twofa("acc-not-forget")

    assert len(actions) == 1
    assert await fetch_account_twofa_password("acc-not-forget") is None


@pytest.mark.parametrize(
    ("error_type", "error_message", "expected"),
    [
        pytest.param(
            "TwoFactorGatewayError",
            "twofa_removal_unconfirmed",
            "twofa_removal_unconfirmed",
            id="refused",
        ),
        # The counter-case, and the reason the gateway had to be the one to decide:
        # reported as ``ok`` — which is what a verb-blind ``EMAIL_UNCONFIRMED``
        # handler produces — this flow clears the column on the way past, because it
        # never reads the flag. The parametrised pair is the whole point.
        pytest.param(None, None, None, id="reported-ok"),
    ],
)
@pytest.mark.asyncio
async def test_a_removal_answered_email_unconfirmed_must_not_clear_the_column(
    monkeypatch: pytest.MonkeyPatch,
    error_type: str | None,
    error_message: str | None,
    expected: str | None,
) -> None:
    """C1 from the SERVICE side: the pairing no test in this repo covered.

    ``remove_account_twofa`` hands its result straight to ``raise_for_result`` and
    never looks at ``twofa_email_unconfirmed`` — only ``set_account_twofa`` does — so
    an ``ok`` carrying that flag is indistinguishable from a real removal HERE and the
    column is cleared, destroying the only copy of a cloud password that may well
    still be live. Nothing downstream can compensate; the refusal has to be raised
    where the verb is known.
    """
    await account_with_password("acc-rm-unconfirmed")
    patch_read(monkeypatch, status(has_password=True), status(has_password=True))
    patch_log(monkeypatch)

    async def _answer(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        if error_type is None:
            return ok_result(account_id, twofa_email_unconfirmed=True)
        return ActionResult(
            status="failed",
            action_type="set_twofa_password",
            account_id=account_id,
            error_type=error_type,
            error_message=error_message,
        )

    monkeypatch.setattr("services.accounts.twofa.execute", _answer)

    if expected is None:
        await remove_account_twofa("acc-rm-unconfirmed")
        # Documented, not endorsed: this is the loss the gateway refusal prevents.
        assert await fetch_account_twofa_password("acc-rm-unconfirmed") is None
        return

    with pytest.raises(AccountActionError) as excinfo:
        await remove_account_twofa("acc-rm-unconfirmed")

    assert excinfo.value.code == expected
    assert await fetch_account_twofa_password("acc-rm-unconfirmed") == STORED
