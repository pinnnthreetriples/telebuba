"""The spam probe is a fenced dispatch, and both of its fences must hold.

Every other test patches ``_seams.refresh_spam_status`` — that is what the seam
module is for — so its body had never executed and mutmut reported the whole
function as having no covering test. The ban and onboarding paths decide an
account's fate on this verdict, so a probe issued by a listener generation that
has already been replaced is exactly as unsafe as a gateway write.

Mirrors ``tests/services/warming/test_runtime_ownership.py``'s lease test.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from services.neurocomment import _seams

if TYPE_CHECKING:
    from schemas.spam_status import SpamStatusVerdict

pytestmark = pytest.mark.usefixtures("isolate_runtime")


def _verdict(account_id: str) -> SpamStatusVerdict:
    from schemas.spam_status import SpamStatusVerdict  # noqa: PLC0415

    return SpamStatusVerdict(account_id=account_id, status="clean", checked_at="t")


@pytest.mark.asyncio
async def test_generation_is_checked_before_and_after_the_spam_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = True
    calls: list[bool] = []

    async def probe(account_id: str, *, force: bool = False) -> SpamStatusVerdict:
        nonlocal current
        calls.append(force)
        current = False  # the listener was replaced while Telegram answered
        return _verdict(account_id)

    monkeypatch.setattr(_seams, "_refresh_spam_status", probe)
    monkeypatch.setattr(_seams, "_account_is_available", lambda _account_id: _true())

    with _seams.generation_scope(lambda: current):
        # Live going in, stale by the time the probe returned: the verdict exists
        # but belongs to a generation that no longer owns the account.
        with pytest.raises(_seams.NeurocommentLeaseRevokedError):
            await _seams.refresh_spam_status("acc-1", force=True)
        # Already stale: refused before the probe, so no Telegram traffic at all.
        with pytest.raises(_seams.NeurocommentLeaseRevokedError):
            await _seams.refresh_spam_status("acc-1")

    assert calls == [True], "the second call must never reach the probe"


async def _true() -> bool:
    return True


@pytest.mark.asyncio
async def test_an_unavailable_account_is_refused_without_probing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A warming or deleted account must not be probed at all."""
    probed = False

    async def probe(account_id: str, *, force: bool = False) -> SpamStatusVerdict:  # noqa: ARG001
        nonlocal probed
        probed = True
        return _verdict(account_id)

    async def _false() -> bool:
        return False

    monkeypatch.setattr(_seams, "_refresh_spam_status", probe)
    monkeypatch.setattr(_seams, "_account_is_available", lambda _account_id: _false())

    with pytest.raises(_seams.NeurocommentAccountUnavailableError):
        await _seams.refresh_spam_status("acc-1")

    assert not probed
