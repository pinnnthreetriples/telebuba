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

# Stable snake_case codes: the API reports them verbatim and the SPA owns the wording
# (``shell.code.*``). Locale-neutral prose in a refusal would be untranslatable.
DISCOVERY_CODE = "account_running_discovery"
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


def assert_account_free(account_id: str) -> None:
    """Raise ``AccountUnavailableError`` when neurocomment already owns this account.

    Synchronous by construction: both answers are in-process reads, so the caller can
    make them inside its per-account lifecycle lock without adding an await for a
    concurrent start to slip through.
    """
    if _discovery_holds(account_id):
        raise AccountUnavailableError(DISCOVERY_CODE, account_id)
    if _is_cooling(account_id):
        raise AccountUnavailableError(COOLING_CODE, account_id)
