"""Start-time mutual exclusion between warming and the neurocomment read traffic.

``start_warming`` already refuses the account that IS the running listener. That guard
alone leaves two ways for a second paced Telegram stream to land on one session:

* a channel-discovery run. It is allowed to start only while the listener is stopped —
  which is exactly the state warming's listener check passes — and then reads for
  minutes. Its claim lives in process memory (``_discovery_state._RUN_ACCOUNTS``), so
  the only honest answer is to ask the module that holds it.
* a live cooldown. Discovery and the comment engine both park a flooded account through
  ``services.neurocomment._state.set_cooldown``; warming looked only at its own
  ``flood_wait_until`` (through the trust penalty) and would happily open a fresh cycle
  on an account Telegram is actively rate-limiting.

Both facts belong to neurocomment, so both are read from it. The imports are deferred:
``services.neurocomment.engine`` imports ``services.warming.pacing`` at module level, so
a load-time import the other way closes the cycle — the same reason neurocomment reaches
back for ``account_lock`` inside its functions.

Split out of ``_runtime`` for the file-size budget, not for layering.
"""

from __future__ import annotations

from datetime import UTC, datetime

from schemas.neurocomment_discovery import DISCOVERY_BUSY_CODE

# Stable snake_case codes: the API reports them verbatim and the SPA owns the wording
# (``shell.code.*``). Locale-neutral prose in a refusal would be untranslatable. The
# discovery one comes from ``schemas`` because the listener's start reports the identical
# refusal; a copy here would be a second string to translate and forget.
DISCOVERY_CODE = DISCOVERY_BUSY_CODE
COOLING_CODE = "account_cooling"


class AccountUnavailableError(ValueError):
    """Refuse to start warming while another runtime or Telegram holds the account.

    Carries a ``code`` instead of splitting into one class per condition: both are the
    same 409 answer ("not now, and here is who has it"), and the code is what the
    operator's toast is keyed on. The reciprocal of discovery's ``account_busy`` /
    ``account_cooling`` start statuses.
    """

    def __init__(self, code: str, account_id: str) -> None:
        self.code = code
        super().__init__(f"{code}: {account_id}")


def _discovery_holds(account_id: str) -> bool:
    from services.neurocomment import _discovery_state  # noqa: PLC0415 - import cycle.

    return _discovery_state.account_busy(account_id)


def _is_cooling(account_id: str) -> bool:
    from services.neurocomment._state import in_cooldown  # noqa: PLC0415 - import cycle.

    # The in-memory map, not the table: it is the hot path every other reader uses, it is
    # rehydrated from ``neurocomment_cooldowns`` at startup, and ``set_cooldown`` writes
    # it BEFORE the durable row, so it is never the staler of the two.
    return in_cooldown(account_id, datetime.now(UTC))


def assert_no_discovery_run(account_id: str) -> None:
    """Raise ``AccountUnavailableError`` while a discovery run reads with this account.

    Not escapable, exactly like the running-listener refusal it sits beside: two paced
    streams on one session is not a health opinion the operator can overrule, it is the
    thing warming exists to avoid.

    Synchronous by construction: the answer is an in-process read, so the caller can make
    it inside its per-account lifecycle lock without adding an await for a concurrent
    start to slip through.
    """
    if _discovery_holds(account_id):
        raise AccountUnavailableError(DISCOVERY_CODE, account_id)


def assert_not_cooling(account_id: str) -> None:
    """Raise ``AccountUnavailableError`` while Telegram is rate-limiting this account.

    Called only where ``enforce_readiness`` is on, because that is the switch the
    operator already has for warming's own flood wait — which reaches the start path as a
    trust penalty inside ``evaluate_readiness``. Ungated, the two disagreed: the operator
    could override warming's own flood deadline but not neurocomment's cooldown, and the
    cooldown window is whatever Telegram said (hours, on a premium wait) and survives a
    restart. Nursing a just-flooded account back is warming's whole job, so the runtime
    that exists to avoid freezes must not be the one runtime that cannot be told to try.
    """
    if _is_cooling(account_id):
        raise AccountUnavailableError(COOLING_CODE, account_id)
