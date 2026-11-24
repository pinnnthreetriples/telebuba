"""An unproven (seller) password is not "stored", and a refused one is remembered.

``twofa_password`` only ever holds a password Telegram accepted; ``twofa_password_pending``
holds a CANDIDATE (a staged write, or the seller's JSON password, unverified at import).
The view tells them apart, a definitive refusal of the candidate is written to the row,
and with no current password a refused candidate answers ``twofa_candidate_refused``
instead of the misleading ``twofa_pending_unsettled``.
"""

from __future__ import annotations

import pytest

from core.db import (
    create_account,
    fetch_account_twofa_pair,
    set_account_twofa_password,
    set_account_twofa_pending,
)
from core.repositories._accounts_twofa import fetch_account_twofa_state
from core.repositories.proxies import create_and_assign_proxy
from core.telegram_client import TelegramReadError
from schemas.accounts import AccountCreate
from schemas.proxy import ProxyCreateAssignment
from schemas.telegram_actions import ActionResult
from schemas.telegram_actions_twofa import SetTwoFactorPassword, VerifyTwoFactorPassword
from schemas.twofa import AccountTwoFactorUpdateRequest
from services.accounts import (
    AccountActionError,
    read_account_twofa,
    record_account_twofa,
    remove_account_twofa,
    set_account_twofa,
)
from services.accounts.twofa import authorising_twofa_password
from tests.services.accounts._twofa_support import STORED, ok_result, patch_log, patch_read, status

_SELLER = "seller-json-pw"
_UNSETTLED = "twofa_pending_unsettled"
_REFUSED = "twofa_candidate_refused"


async def _staged(account_id: str, *, current: str | None = None) -> None:
    await create_account(AccountCreate(account_id=account_id))
    await create_and_assign_proxy(
        ProxyCreateAssignment(
            proxy_type="socks5", host="10.0.0.9", port=1080, account_id=account_id
        ),
    )
    if current is not None:
        await set_account_twofa_password(account_id, current)
    await set_account_twofa_pending(account_id, _SELLER)


async def _attempt(verb: str, account_id: str) -> object:
    if verb == "set":
        return await set_account_twofa(
            account_id, AccountTwoFactorUpdateRequest(password="third-pw-123")
        )
    return await remove_account_twofa(account_id)


def _verify_answers(
    monkeypatch: pytest.MonkeyPatch, answer: str, *, accepts: str | None = None
) -> list[str]:
    """Every verify answers ``answer`` (``refused`` / ``unavailable``); ``accepts`` is ok."""
    asked: list[str] = []

    async def _execute(account_id: str, action: object) -> ActionResult:
        if isinstance(action, VerifyTwoFactorPassword):
            asked.append(action.password)
            if action.password == accepts:
                return ok_result(account_id, "verify_twofa_password")
            if answer == "refused":
                return ActionResult(
                    status="failed",
                    action_type="verify_twofa_password",
                    account_id=account_id,
                    error_type="TwoFactorGatewayError",
                    error_message="twofa_current_password_invalid",
                )
            return ActionResult(
                status="unavailable", action_type="verify_twofa_password", account_id=account_id
            )
        assert isinstance(action, SetTwoFactorPassword)
        return ok_result(account_id)

    monkeypatch.setattr("services.accounts.twofa.execute", _execute)
    return asked


# --- the view -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_view_for_a_verified_current_password(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="v-current"))
    await set_account_twofa_password("v-current", STORED)
    patch_read(monkeypatch, status(has_password=True))

    view = await read_account_twofa("v-current")

    assert (view.has_stored_password, view.has_unverified_candidate, view.candidate_refused) == (
        True,
        False,
        False,
    )


@pytest.mark.asyncio
async def test_a_staged_seller_password_is_a_candidate_not_a_stored_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _staged("v-seller")
    patch_read(monkeypatch, status(has_password=True))

    view = await read_account_twofa("v-seller")

    assert (view.has_stored_password, view.has_unverified_candidate, view.candidate_refused) == (
        False,
        True,
        False,
    )


@pytest.mark.asyncio
async def test_the_view_says_so_when_telegram_refused_the_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _staged("v-refused")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    _verify_answers(monkeypatch, "refused")
    assert await authorising_twofa_password("v-refused") is None

    view = await read_account_twofa("v-refused")

    assert (view.has_stored_password, view.has_unverified_candidate, view.candidate_refused) == (
        False,
        True,
        True,
    )


@pytest.mark.asyncio
async def test_a_current_password_beside_a_different_pending_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _staged("v-both", current=STORED)
    patch_read(monkeypatch, status(has_password=True))

    view = await read_account_twofa("v-both")

    assert (view.has_stored_password, view.has_unverified_candidate, view.candidate_refused) == (
        True,
        True,
        False,
    )


@pytest.mark.asyncio
async def test_a_pending_value_equal_to_the_current_one_is_no_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="v-same"))
    await set_account_twofa_password("v-same", STORED)
    await set_account_twofa_pending("v-same", STORED)
    patch_read(monkeypatch, status(has_password=True))

    view = await read_account_twofa("v-same")

    assert (view.has_stored_password, view.has_unverified_candidate) == (True, False)


@pytest.mark.asyncio
async def test_the_view_facts_survive_a_failed_live_read(monkeypatch: pytest.MonkeyPatch) -> None:
    await _staged("v-down")

    async def _down(account_id: str, action: object) -> None:  # noqa: ARG001
        msg = "unavailable: ConnectionError"
        raise TelegramReadError(msg)

    monkeypatch.setattr("services.accounts.twofa.execute_read", _down)

    view = await read_account_twofa("v-down")

    assert view.error is not None
    assert (view.has_stored_password, view.has_unverified_candidate) == (False, True)


# --- the mark -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_definitive_refusal_of_the_candidate_is_written_to_the_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _staged("m-refused")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    _verify_answers(monkeypatch, "refused")

    assert await authorising_twofa_password("m-refused") is None

    state = await fetch_account_twofa_state("m-refused")
    # The candidate itself is never dropped on a refusal: it may be the only copy.
    assert (state.current, state.pending, state.pending_refused) == (None, _SELLER, True)


@pytest.mark.asyncio
async def test_a_refused_current_and_a_refused_candidate_mark_the_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _staged("m-both", current=STORED)
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    asked = _verify_answers(monkeypatch, "refused")

    await authorising_twofa_password("m-both")

    assert asked == [STORED, _SELLER]
    assert (await fetch_account_twofa_state("m-both")).pending_refused is True


@pytest.mark.asyncio
async def test_an_unknown_verdict_writes_no_mark(monkeypatch: pytest.MonkeyPatch) -> None:
    await _staged("m-unknown")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    asked = _verify_answers(monkeypatch, "unavailable")

    await authorising_twofa_password("m-unknown")

    assert asked == [_SELLER]
    assert (await fetch_account_twofa_state("m-unknown")).pending_refused is False


@pytest.mark.asyncio
async def test_a_read_that_failed_asks_nothing_and_writes_no_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _staged("m-no-read")

    async def _down(account_id: str, action: object) -> None:  # noqa: ARG001
        msg = "unavailable: ConnectionError"
        raise TelegramReadError(msg)

    monkeypatch.setattr("services.accounts.twofa.execute_read", _down)
    patch_log(monkeypatch)
    asked = _verify_answers(monkeypatch, "refused")

    await authorising_twofa_password("m-no-read")

    assert asked == []
    assert (await fetch_account_twofa_state("m-no-read")).pending_refused is False


@pytest.mark.asyncio
async def test_a_candidate_telegram_accepts_is_promoted_and_unmarked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _staged("m-accepted")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    _verify_answers(monkeypatch, "refused", accepts=_SELLER)

    assert await authorising_twofa_password("m-accepted") == _SELLER

    assert await fetch_account_twofa_state("m-accepted") == (_SELLER, None, False)


@pytest.mark.asyncio
async def test_a_candidate_dropped_because_telegram_has_no_password_loses_its_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _staged("m-gone")
    patch_read(monkeypatch, status(has_password=True), status(has_password=False))
    patch_log(monkeypatch)
    _verify_answers(monkeypatch, "refused")
    await authorising_twofa_password("m-gone")
    assert (await fetch_account_twofa_state("m-gone")).pending_refused is True

    await authorising_twofa_password("m-gone")  # the live read now says: no password at all

    assert await fetch_account_twofa_state("m-gone") == (None, None, False)


@pytest.mark.asyncio
async def test_recording_the_real_password_verifies_it_and_clears_the_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _staged("m-record")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    _verify_answers(monkeypatch, "refused", accepts="the-real-password")
    await authorising_twofa_password("m-record")
    assert (await fetch_account_twofa_state("m-record")).pending_refused is True

    assert await record_account_twofa("m-record", "the-real-password") is True

    assert await fetch_account_twofa_state("m-record") == ("the-real-password", None, False)


@pytest.mark.asyncio
async def test_forgetting_the_stored_password_clears_the_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _staged("m-forget")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    asked = _verify_answers(monkeypatch, "refused")
    await authorising_twofa_password("m-forget")
    asked.clear()

    await remove_account_twofa("m-forget", forget_only=True)

    assert asked == []
    assert await fetch_account_twofa_state("m-forget") == (None, None, False)


# --- the refusal codes ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_set_over_a_refused_candidate_says_it_was_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _staged("c-set")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    _verify_answers(monkeypatch, "refused")

    with pytest.raises(AccountActionError) as caught:
        await set_account_twofa("c-set", AccountTwoFactorUpdateRequest(password="third-pw-123"))

    assert caught.value.code == _REFUSED
    assert await fetch_account_twofa_pair("c-set") == (None, _SELLER)


@pytest.mark.asyncio
async def test_the_refused_code_survives_the_recheck_throttle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second attempt is not put to Telegram again, yet still names the refusal."""
    await _staged("c-throttle")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    asked = _verify_answers(monkeypatch, "refused")
    request = AccountTwoFactorUpdateRequest(password="third-pw-123")

    for _ in range(3):
        with pytest.raises(AccountActionError) as caught:
            await set_account_twofa("c-throttle", request)
        assert caught.value.code == _REFUSED

    assert asked == [_SELLER]


@pytest.mark.asyncio
async def test_a_removal_over_a_refused_candidate_says_it_was_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _staged("c-remove")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    _verify_answers(monkeypatch, "refused")

    with pytest.raises(AccountActionError) as caught:
        await remove_account_twofa("c-remove")

    assert caught.value.code == _REFUSED
    assert await fetch_account_twofa_pair("c-remove") == (None, _SELLER)


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["set", "remove"])
async def test_an_unproven_candidate_is_still_unsettled_not_refused(
    monkeypatch: pytest.MonkeyPatch, verb: str
) -> None:
    await _staged(f"c-unknown-{verb}")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    _verify_answers(monkeypatch, "unavailable")

    with pytest.raises(AccountActionError) as caught:
        await _attempt(verb, f"c-unknown-{verb}")

    assert caught.value.code == _UNSETTLED


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["set", "remove"])
async def test_with_a_current_password_a_refused_candidate_stays_unsettled(
    monkeypatch: pytest.MonkeyPatch, verb: str
) -> None:
    """Both stored values refused: the card is not "candidate refused", a current exists."""
    account_id = f"c-current-{verb}"
    await _staged(account_id, current=STORED)
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    _verify_answers(monkeypatch, "refused")

    with pytest.raises(AccountActionError) as caught:
        await _attempt(verb, account_id)

    assert caught.value.code == _UNSETTLED
    assert await fetch_account_twofa_pair(account_id) == (STORED, _SELLER)


@pytest.mark.asyncio
async def test_neither_code_nor_log_carries_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    await _staged("c-secret")
    patch_read(monkeypatch, status(has_password=True))
    events = patch_log(monkeypatch)
    _verify_answers(monkeypatch, "refused")

    with pytest.raises(AccountActionError) as caught:
        await remove_account_twofa("c-secret")

    assert _SELLER not in str(caught.value)
    assert _SELLER not in str(events)
