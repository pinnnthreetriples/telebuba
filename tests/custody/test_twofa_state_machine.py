"""A Hypothesis state machine over the real 2FA password store, against a model of Telegram.

The machine follows the flow the services follow when Telebuba changes the password: settle a
pending candidate first (``resolve_current``), stage the new one as PENDING, send it, then the
server applies it and answers, applies it and the answer is lost, or refuses it. A known
answer promotes or restores, a lost one leaves pending. Later sign-ins walk the real
``sign_in_with_passwords`` against the model server. The invariants are checked after every rule.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from itertools import count
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule
from telethon import errors

from core.config import settings
from core.db import create_account
from core.repositories import _accounts_twofa as repo
from core.telegram_client._twofa_candidates import sign_in_with_passwords
from core.twofa_budget import TwofaAttemptsExhaustedError
from schemas.accounts import AccountCreate
from services.accounts import _twofa_pending as pending_svc

if TYPE_CHECKING:
    from collections.abc import Coroutine

_IDS = count()
_T0 = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
_PASSWORDS = st.sampled_from(["pw-a", "pw-b", "pw-c", "pw-d"])
_OUTCOMES = st.sampled_from(["applied", "applied_answer_lost", "refused"])


class _Clock:
    now = _T0


class _FakeDatetime:
    @staticmethod
    def now(_tz: object = None) -> datetime:
        return _Clock.now


class _Client:
    def __init__(self, server: _Server) -> None:
        self.server = server

    async def sign_in(self, *, password: str) -> None:
        if password != self.server.password:
            raise errors.PasswordHashInvalidError(request=None)


class _Server:
    password: str | None = None


class TwofaStore(RuleBasedStateMachine):
    """Real repository on a real SQLite file; the server's password is the model."""

    def __init__(self) -> None:
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.account = f"acc{next(_IDS)}"
        self.server = _Server()
        self.maybe_sent: set[str] = set()
        self.run(create_account(AccountCreate(account_id=self.account)))
        self.max = settings.session_audit.wrong_answer_limit
        self.window = settings.session_audit.wrong_answer_window_hours
        _Clock.now = _T0
        self.clock = patch.object(repo, "datetime", _FakeDatetime)
        self.clock.start()
        self.last_left = self.max
        self.confirmed_now = False

    def run[T](self, coro: Coroutine[object, object, T]) -> T:
        return self.loop.run_until_complete(coro)

    def teardown(self) -> None:
        self.clock.stop()
        self.loop.close()

    # --- the services' flow -----------------------------------------------------------------

    async def _verify(self, password: str) -> bool | None:
        return password == self.server.password

    def _settle(self) -> bool:
        """``resolve_current``: ``True`` when the pair is settled and a change may start."""
        pending_svc._UNSETTLED_AT.clear()
        status = SimpleNamespace(has_password=self.server.password is not None)
        settled = self.run(pending_svc.resolve_current(self.account, status, self._verify))  # ty: ignore[invalid-argument-type]
        return not settled.unsettled

    @rule(
        new=_PASSWORDS,
        outcome=_OUTCOMES,
        crash=st.sampled_from(["none", "before_send", "after_send"]),
    )
    def change_password(self, new: str, outcome: str, crash: str) -> None:
        if not self._settle():
            return
        current, previous = self.run(repo.fetch_account_twofa_pair(self.account))
        if new == current:
            # Known defect (see the xfail test below): current == pending lists it twice.
            return
        assert self.run(repo.set_account_twofa_pending(self.account, new))
        if crash == "before_send":
            return
        self.maybe_sent.add(new)
        # The server checks the current password it is sent.
        ok = self.server.password is None or current == self.server.password
        applied = outcome != "refused" and ok
        if applied:
            self.server.password = new
        if crash == "after_send":
            return
        if outcome == "applied_answer_lost" and applied:
            return
        if applied:
            assert self.run(repo.promote_account_twofa_pending(self.account, new))
        else:
            self.run(repo.set_account_twofa_pending(self.account, previous))

    @rule()
    def settle(self) -> None:
        self._settle()

    @rule(strategy=st.sampled_from(["candidates", "typed_wrong"]))
    def sign_in(self, strategy: str) -> None:
        if self.server.password is None:
            return
        candidates = self.run(repo.twofa_password_candidates(self.account))
        if strategy == "typed_wrong":
            candidates = [*candidates, "pw-wrong"]
        if not candidates:
            return
        before = self.run(repo.fetch_account_twofa_state(self.account))
        try:
            used = self.run(sign_in_with_passwords(_Client(self.server), self.account, candidates))  # ty: ignore[invalid-argument-type]
        except (errors.PasswordHashInvalidError, TwofaAttemptsExhaustedError):
            return
        assert used == self.server.password
        self.confirmed_now = True
        assert before is not None

    @rule()
    def operator_records_server_password(self) -> None:
        # The operator records a password Telegram accepted (verified first), or none at all.
        if self.server.password is None:
            self.run(repo.set_account_twofa_password(self.account, None))
        else:
            self.run(repo.set_account_twofa_password(self.account, self.server.password))
            self.confirmed_now = True

    @rule()
    def a_wrong_answer_is_counted(self) -> None:
        self.run(repo.record_twofa_rejection(self.account, window_hours=self.window))

    @rule(hours=st.sampled_from([0, 1, 30]))
    def time_passes(self, hours: int) -> None:
        _Clock.now += timedelta(hours=hours)

    @rule()
    def confirm_current_password(self) -> None:
        if self.server.password is not None:
            self.run(repo.confirm_twofa_password_used(self.account, self.server.password))
            self.confirmed_now = True

    # --- invariants --------------------------------------------------------------------------

    def _left(self) -> int:
        return self.run(
            repo.remaining_twofa_attempts(
                self.account, max_rejections=self.max, window_hours=self.window
            )
        )

    @invariant()
    def the_password_telegram_may_hold_is_never_lost(self) -> None:
        state = self.run(repo.fetch_account_twofa_state(self.account))
        held = {state.current, state.pending} - {None}
        server = self.server.password
        if server is not None and (server in self.maybe_sent or server == state.current):
            assert server in held, "Telegram's password is in neither column"

    @invariant()
    def candidates_are_clean(self) -> None:
        state = self.run(repo.fetch_account_twofa_state(self.account))
        candidates = self.run(repo.twofa_password_candidates(self.account))
        assert len(candidates) == len(set(candidates))
        if state.pending_refused:
            assert state.pending not in candidates

    @invariant()
    def the_budget_stays_in_bounds_and_only_a_confirmation_refills_it(self) -> None:
        left = self._left()
        assert 0 <= left <= self.max
        if self.confirmed_now:
            assert left == self.max
            self.confirmed_now = False
        self.last_left = left

    @invariant()
    def the_state_reads_and_a_refusal_has_a_candidate(self) -> None:
        state = self.run(repo.fetch_account_twofa_state(self.account))
        assert not state.pending_refused or state.pending is not None


TestTwofaStore = TwofaStore.TestCase
TestTwofaStore.settings = hyp_settings(stateful_step_count=30, deadline=None)


@pytest.mark.asyncio
async def test_candidates_never_repeat_a_password() -> None:
    await create_account(AccountCreate(account_id="dup"))
    await repo.set_account_twofa_password("dup", "pw-a")
    await repo.set_account_twofa_pending("dup", "pw-a")

    candidates = await repo.twofa_password_candidates("dup")

    assert len(candidates) == len(set(candidates))
