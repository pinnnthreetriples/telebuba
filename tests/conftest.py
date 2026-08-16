"""Fixtures every test gets, whatever the operator's ``.env`` happens to hold.

The root ``conftest.py`` stays free of pytest imports (deptry flags a dev dependency
reaching production code), so suite-wide fixtures live here instead — this file
covers everything under ``tests/`` and pytest applies it before any subpackage
conftest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from telethon.client.telegrambaseclient import TelegramBaseClient

from core.config import settings
from services import _account_owner, _join_lock, pacing
from services.neuroshilling import _state as neuroshilling_state

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _no_telegram_connection(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail any test that opens a real Telegram connection, for EVERY test.

    Telethon's ``connect()`` is the one place a socket is opened, so a test that
    stubs some domain seams but not all the ones its code path reaches dies here
    instead of dialling Telegram. ``services.neurocomment.bans`` was the find:
    ``tests/services/test_neurocomment_bans.py`` stubbed ``_seams.execute_read``
    and not ``_seams.refresh_spam_status``, and four of its tests connected for
    real.

    The credentials are deliberately NOT blanked the way
    ``_no_ambient_deepseek_key`` blanks its key. ``settings.telegram.api_id`` is
    read as configuration as well as credential — ``check_telegram_session`` and
    the session-check tests branch on ``api_id == 0`` — so emptying it suite-wide
    would change what those tests exercise. It is also what made this escape
    invisible on CI: with no credentials Telethon refuses to construct a client at
    all, the gateway degrades the failure to ``unknown``, and the test passes
    quietly. Blocking the CALL instead keeps construction paths live and makes the
    escape loud on both machines.

    ``pytest.fail`` raises ``Failed``, which derives from ``BaseException`` — that
    is the point. Every path to the gateway degrades a failed probe behind
    ``except Exception``, so a normal error would be swallowed and the escape would
    stay silent, merely faster. A test that genuinely means to reach Telegram must
    say so by overriding this fixture.
    """

    async def _fail(_self: TelegramBaseClient, *_args: object, **_kwargs: object) -> None:
        pytest.fail(
            f"{request.node.nodeid} opened a real Telegram connection — "
            "a seam its code path reaches is not stubbed.",
            pytrace=False,
        )

    monkeypatch.setattr(TelegramBaseClient, "connect", _fail)


@pytest.fixture(autouse=True)
def _isolate_session_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ``session_dir`` at this test's ``tmp_path``, for EVERY test.

    ``settings.telegram.session_dir`` defaults to the relative ``Path("sessions")``,
    which resolves against the CWD — for a test run, the repo root, i.e. the same
    directory name the operator's live instance keeps real credentials in. Three
    subpackage conftests (``tests/api``, ``tests/core/telegram_client``,
    ``tests/services/accounts``) already redirected it; everything else wrote there.
    They kept their own redirect: this one is the floor, not a replacement.

    Serially the escape was invisible because ``*.session`` is gitignored. Under
    ``pytest -n auto`` it is not: every worker composes the same absolute path, so
    the warming ``remove_account`` tests raced each other's unlink of one shared
    ``sessions/acc-1.session`` — the loser's ``remove_account`` died before
    ``delete_account``, leaving the row in place, and the test that asserts the
    concurrent ``start_warming`` raises ``UnknownAccountError`` failed instead.
    Isolating the directory suite-wide fixes the race and stops the leak at once.
    """
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")


@pytest.fixture(autouse=True)
def _no_ambient_deepseek_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test as a deployment that has not configured DeepSeek.

    That key is the whole switch deciding who generates text, and it is read from the
    operator's ``.env`` — which made the suite depend on ambient config. With a key
    present, every test that stubs only ``_seams.generate_text`` routed to the
    UNSTUBBED ``generate_text_deepseek`` and issued live HTTPS calls to
    api.deepseek.com: 25 of them in one run, each waiting out a 30s timeout. CI has no
    key, so CI stayed green while local runs crawled and reached the network. The
    divergence is the defect here, not the slowness.

    Blanked centrally rather than patched into each stub helper, so no later test can
    reopen the hole by stubbing one provider and forgetting the other. A test that
    means to exercise DeepSeek sets the key itself and stubs both
    (``tests/services/neurocomment/test_llm_routing.py``).
    """
    monkeypatch.setattr(settings.deepseek, "api_key", "")


@pytest.fixture(autouse=True)
def _reset_pacing() -> Iterator[None]:
    """Empty ``services.pacing`` around EVERY test, not just the ones that opt in.

    Its per-key cache holds ``asyncio.Lock`` objects, and a lock is bound to the
    loop that created it: one left behind by an async test makes the next test
    that paces the same key raise ``RuntimeError`` about a different loop. Scoped
    suite-wide rather than per-domain because the failure is order-dependent and
    lands on whoever touches a paced seam next, not on whoever left the lock.

    Chosen over keying the cache by running loop: that would put test-only
    bookkeeping — and a map that grows one entry per loop ever created — into
    production code to solve a problem only tests have.
    """
    pacing.reset_for_tests()
    yield
    pacing.reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_join_locks() -> Iterator[None]:
    """Empty the per-account join mutexes around EVERY test.

    Same failure as ``_reset_pacing`` and the same reach: the map holds
    ``asyncio.Lock`` objects, a lock belongs to the loop that first waited on it, and
    both features join under these — so one left behind by a neuroshilling test lands
    on the next neurocomment test to onboard the same account id.
    """
    _join_lock.reset_for_tests()
    yield
    _join_lock.reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_account_owner() -> Iterator[None]:
    """Empty the account-ownership registry around EVERY test.

    Suite-wide for the same reason as ``_reset_pacing``, and now more urgently: real
    production code writes it. Every warming start and every restart-reconcile claims
    its account through ``_spawn_runtime_task``, and a task the test abandoned without
    letting its done-callback run leaves the claim behind. The next test to select a
    neurocomment account, or to start warming on the same id, would then be refused by
    a campaign that never existed — an order-dependent failure landing on an innocent
    test, which is exactly what a suite-wide reset is for.
    """
    _account_owner.reset_for_tests()
    yield
    _account_owner.reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_neuroshilling_generation() -> Iterator[None]:
    """Empty the neuroshilling LLM budget and single-flight set around EVERY test.

    Suite-wide for the same reason as the two above: the budget is a fleet-wide
    rolling window, so a test that generates leaves a call behind and the next one
    to check the cap sees a number nobody in it produced. With
    ``max_llm_calls_per_day`` deliberately small in a test, that is an
    order-dependent refusal landing on an innocent test.
    """
    neuroshilling_state.reset_for_tests()
    yield
    neuroshilling_state.reset_for_tests()
