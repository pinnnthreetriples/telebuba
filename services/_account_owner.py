"""Which feature is holding an account right now — one tiny in-memory registry.

Shape copied from :mod:`services.neurocomment._discovery_state`: one module,
synchronous functions with no ``await`` in them, nothing persisted. A single
uvicorn worker means a single event loop, so a function that contains no
``await`` cannot be straddled by a second caller — that is what makes a claim
atomic here without a lock, and it holds only as long as the callers keep the
claim and the point of no return in the same await-free stretch.

**The holder is an identity, not a flag.** ``holder`` is warming's ``run_id`` and
neuroshilling's ``campaign_id``; :func:`release` gives the claim up only when
BOTH the owner and the holder match. That is what stops a late done-callback from
an evicted generation releasing the claim its successor now holds — the same
identity check ``services.warming._seams.revoke_lease`` already makes on leases.

**There are two owners, not three.** Warming and neuroshilling write; neurocomment
only ever reads. Neuroshilling asks the DATABASE whether an account is serving an
active neurocomment campaign. Drift between "the registry says free" and "the
feature is using it" therefore cannot arise for neurocomment, because no line of
code ever writes that value.

In-memory on purpose: nothing here outlives the process, a restart is a full
repair, and a table would need its own migration plus its own garbage collector
for state that is worth less than the ceremony.
"""

from __future__ import annotations

from typing import Literal

Owner = Literal["warming", "neuroshilling"]

# account_id -> (owner, holder)
_OWNED: dict[str, tuple[Owner, str]] = {}


def owner_of(account_id: str) -> Owner | None:
    """Which feature holds this account, or ``None`` when nothing does."""
    held = _OWNED.get(account_id)
    return None if held is None else held[0]


def try_claim(account_id: str, owner: Owner, holder: str) -> Owner | None:
    """Claim ``account_id``; return ``None`` on success, else the current owner.

    Idempotent for the same ``(owner, holder)`` pair, so a caller that re-enters
    its own claim path is not refused by itself. A DIFFERENT holder of the same
    owner is refused just like a different owner would be — that is what enforces
    "one running campaign per account" without any extra state.
    """
    held = _OWNED.get(account_id)
    if held is not None and held != (owner, holder):
        return held[0]
    _OWNED[account_id] = (owner, holder)
    return None


def take_over(account_id: str, owner: Owner, holder: str) -> None:
    """Claim ``account_id`` for ``(owner, holder)``, evicting whatever held it.

    For the one caller that must not be refusable. Warming's ``_spawn_runtime_task``
    re-spawns the SAME account under a NEW ``run_id`` — that is what "start now" and
    restart reconciliation both do — and :func:`try_claim` refuses a different holder
    of the same owner on purpose, because that refusal is what keeps two running
    neuroshilling campaigns off one account. Routed through :func:`try_claim`, the
    re-spawn would be refused, the registry would keep naming the dead run, and the
    new generation's identity-checked :func:`release` would never match again: the
    account would stay held until the process restarted.

    Safe only because the refusal already happened above it: ``start_warming`` raises
    before it spawns, and the only other caller — restart reconciliation — runs before
    anything in this process can have claimed. A caller that CAN be told no must use
    :func:`try_claim`.
    """
    _OWNED[account_id] = (owner, holder)


def release(account_id: str, owner: Owner, holder: str) -> None:
    """Give up a claim, but only if this exact ``(owner, holder)`` still holds it."""
    if _OWNED.get(account_id) == (owner, holder):
        del _OWNED[account_id]


def release_owner(owner: Owner) -> None:
    """Drop every claim belonging to ``owner``.

    Startup reconciliation uses this to wipe its own slice of a registry that a
    previous process left nothing of anyway. Scoped by owner so it can never
    erase the other feature's claims.
    """
    for account_id in [key for key, held in _OWNED.items() if held[0] == owner]:
        del _OWNED[account_id]


def owners() -> dict[str, Owner]:
    """A snapshot of ``account_id -> owner`` for the board's account picker."""
    return {account_id: held[0] for account_id, held in _OWNED.items()}


def holder_of(account_id: str) -> str | None:
    """The identity holding this account — a warming run id or a campaign id."""
    held = _OWNED.get(account_id)
    return None if held is None else held[1]


def reset_for_tests() -> None:
    _OWNED.clear()
