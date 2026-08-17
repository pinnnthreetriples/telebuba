"""In-memory state: the LLM budgets, generation single-flight, and the run fences.

Shape copied from :mod:`services.neurocomment._discovery_state` — one module of
synchronous functions, nothing persisted, and the claim taken inside an await-free
section so it is atomic under a single event loop without a lock.

Nothing here is durable on purpose. The budget guards spend within a running
process; a restart forgives it, which is the same bargain the discovery search
counter already makes. A table would need a migration and a sweeper for state
that does not outlive the process.

**Why a budget exists at all.** The project keeps no token accounting anywhere, and
one campaign is ten accounts across twenty targets: a mistyped topic that keeps
being regenerated is a four-figure bill with nothing between it and the card. The
cap is counted in CALLS rather than tokens because calls are what this process can
actually see — and it counts every HTTP request, the gateway's own transient
retries included, since a retry costs exactly what the first try did.
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingRefusalCode

_LLM_WINDOW = timedelta(hours=24)

# Rolling-24h timestamps of provider calls, fleet-wide rather than per campaign:
# the bill is one bill, and ten campaigns each under their own share of it is the
# failure this is here to prevent.
_LLM_CALLS: deque[datetime] = deque()
# Campaigns with a generation in flight. A second click would otherwise spend the
# budget twice and race two writes over the same rows.
_GENERATING: set[str] = set()
# Campaigns with a START in flight. The status column cannot answer this on its own:
# ``running`` is written several awaits after the check that reads it, and both halves
# of a double click straddle that gap.
_STARTING: set[str] = set()

# account_id -> the rolling hour of autoreply ATTEMPTS that account has paid for, and
# target -> the rolling day one CHAT has. Both count drafts rather than published
# answers, because the provider charges for a draft whether or not the output gate lets
# it out — and a busy group hands the poller a full page every thirty seconds, so every
# message on it that wins the dice is billed.
#
# Two windows because they bound two different things. The hourly one is per ACCOUNT,
# which is what the operator's "messages per hour" already describes, and it is that
# same number: an account may not pay for more answers in an hour than it is allowed to
# publish messages in one. On its own that only SPREAD a hostile chat's spend across the
# fleet — ten accounts at ten attempts an hour is the whole ``max_llm_calls_per_day``
# inside the first hour, after which every campaign in the process is refused for the
# rest of the day. The daily one is therefore per TARGET: the unit that turns hostile is
# a chat, and this is what stops one of them deciding for all of them. Keyed on the
# target alone and not on the pair with a campaign, because the chat is one chat however
# many of our fleets watch it — the reading ``claim_chat_reply`` takes of the same
# question.
_REPLY_ATTEMPTS: dict[str, deque[datetime]] = {}
_CHAT_ATTEMPTS: dict[str, deque[datetime]] = {}
_ATTEMPT_WINDOW = timedelta(hours=1)
_CHAT_ATTEMPT_WINDOW = timedelta(hours=24)

# Campaigns already told once that their key is missing. A configuration fault is one
# fact about the campaign, not one fact per observed message, and the alternative was
# a WARNING row per message a busy chat produced — four figures an hour, in the log
# the operator has to read to find anything else.
_KEY_WARNED: set[str] = set()

# campaign_id -> the newest run generation. Bumped by BOTH Start and Stop, and every
# external call of a run checks it before and after itself, so a coroutine parked in a
# step delay when Stop was pressed wakes up fenced. A ``status='stopping'`` flip cannot
# do this: it is a row, and nothing reads a row on the way out of a sleep.
_RUN_GENERATIONS: dict[str, int] = {}
# campaign_id -> the run_id currently entitled to write this campaign's terminal row.
# A SECOND map and not a re-use of the counter above, because the two answer different
# questions: Stop bumps the generation (so the old run stops acting) but the run it
# stopped is still the one that must settle. Only a NEWER run displaces that right,
# which is what stops a late finisher writing ``done`` over its successor's ``running``.
_RUN_OWNER: dict[str, str] = {}


def _prune(now: datetime) -> None:
    cutoff = now - _LLM_WINDOW
    while _LLM_CALLS and _LLM_CALLS[0] < cutoff:
        _LLM_CALLS.popleft()


def at_daily_llm_cap(now: datetime | None = None) -> bool:
    """Has the fleet used up its rolling-24h generation allowance?

    A configured ``0`` reads as "never generate", not as "no limit" — the same way
    neurocomment's search cap reads it, and the only reading that makes a zero
    budget mean anything.
    """
    moment = now or datetime.now(UTC)
    _prune(moment)
    return len(_LLM_CALLS) >= settings.neuroshilling.max_llm_calls_per_day


def record_llm_call(now: datetime | None = None, *, calls: int = 1) -> None:
    """Charge ``calls`` provider calls to the window, all at the same moment.

    Counted in HTTP requests rather than in attempts: the gateway retries a
    transient failure inside one call, so an attempt costs ``max_retries + 1``
    requests and the caller charges that.
    """
    _LLM_CALLS.extend([now or datetime.now(UTC)] * calls)


def _at_attempt_cap(
    windows: dict[str, deque[datetime]],
    key: str,
    cap: int,
    window: timedelta,
    moment: datetime,
) -> bool:
    """Has ``key`` reached ``cap`` attempts inside ``window``? Prunes as it counts."""
    attempts = windows.get(key)
    if attempts is None:
        return False
    cutoff = moment - window
    while attempts and attempts[0] < cutoff:
        attempts.popleft()
    return len(attempts) >= cap


def at_reply_attempt_cap(account_id: str, cap: int, now: datetime | None = None) -> bool:
    """Has this account paid for ``cap`` autoreply drafts in the last rolling hour?

    Read before the claim and charged by :func:`record_reply_attempt` after it, so
    what is counted is the ATTEMPT — the thing the provider bills for — and not the
    published reply the chat log already counts.
    """
    return _at_attempt_cap(
        _REPLY_ATTEMPTS,
        account_id,
        cap,
        _ATTEMPT_WINDOW,
        now or datetime.now(UTC),
    )


def at_chat_attempt_cap(target: str, now: datetime | None = None) -> bool:
    """Has this chat been charged its day's worth of autoreply drafts?

    The ceiling that keeps one conversation from spending the process's day. The cap
    is a deployment setting rather than a campaign field, because what it protects —
    ``max_llm_calls_per_day`` — is also one number for the whole process, and the
    chats sharing it belong to campaigns that know nothing of each other.
    """
    return _at_attempt_cap(
        _CHAT_ATTEMPTS,
        target,
        settings.neuroshilling.max_reply_attempts_per_chat_per_day,
        _CHAT_ATTEMPT_WINDOW,
        now or datetime.now(UTC),
    )


def record_reply_attempt(account_id: str, target: str, now: datetime | None = None) -> None:
    """Charge one autoreply draft to ``account_id``'s hour and to ``target``'s day.

    One call for both windows because one draft is charged to both, and a caller that
    remembered only one of them would leave that ceiling unreachable.
    """
    moment = now or datetime.now(UTC)
    _REPLY_ATTEMPTS.setdefault(account_id, deque()).append(moment)
    _CHAT_ATTEMPTS.setdefault(target, deque()).append(moment)


def first_key_warning(campaign_id: str) -> bool:
    """True the first time this process refuses ``campaign_id`` for a missing key.

    The refusal is worth exactly one log line: the key is a setting, it does not
    change between two messages of the same chat, and the operator reading the log is
    looking for the fault rather than for a count of how often it was met.
    """
    if campaign_id in _KEY_WARNED:
        return False
    _KEY_WARNED.add(campaign_id)
    return True


def try_start_generation(
    campaign_id: str,
    now: datetime | None = None,
) -> NeuroshillingRefusalCode | None:
    """Claim this campaign's generation slot, or say why not.

    Contains no ``await`` by design: everything from the check to the claim is one
    synchronous section, so a second request cannot straddle it. The caller must
    release with :func:`finish_generation` in a ``finally``.

    The cap test here is the door, not a reservation — nothing is charged until a
    call is actually made, so two campaigns clicked together at cap-1 both get in.
    The generation loop re-reads the cap on every pass, which is what stops them.
    """
    if campaign_id in _GENERATING:
        return "generation_in_progress"
    if at_daily_llm_cap(now):
        return "llm_daily_limit_reached"
    _GENERATING.add(campaign_id)
    return None


def finish_generation(campaign_id: str) -> None:
    _GENERATING.discard(campaign_id)


def try_claim_start(campaign_id: str) -> bool:
    """Claim this campaign's start slot; ``False`` means a start is already in flight.

    Same shape as :func:`try_start_generation` and for the same reason: the caller's
    "is this campaign already live?" test reads a column that ``start_campaign`` only
    writes several awaits later — the roster reads and the account claim sit in between
    — so two requests both pass that test. Taking this claim in the SAME synchronous
    section as the test is what makes the pair atomic. The caller must release with
    :func:`finish_start` in a ``finally``.
    """
    if campaign_id in _STARTING:
        return False
    _STARTING.add(campaign_id)
    return True


def finish_start(campaign_id: str) -> None:
    _STARTING.discard(campaign_id)


def start_in_flight(campaign_id: str) -> bool:
    """Is a start of this campaign between its claim and its spawn right now?

    Read by the run task's done callback, which hands a stopped run's roster back: a
    start that has already claimed those accounts but not yet published its task would
    otherwise have them taken from under it.
    """
    return campaign_id in _STARTING


def begin_run(campaign_id: str, run_id: str) -> int:
    """Publish a new run generation for ``campaign_id`` and return it.

    The counter only ever rises, and it is never removed on settle: a reused value
    would let a coroutine from two runs ago pass the fence.
    """
    generation = _RUN_GENERATIONS[campaign_id] = _RUN_GENERATIONS.get(campaign_id, 0) + 1
    _RUN_OWNER[campaign_id] = run_id
    return generation


def revoke_run(campaign_id: str) -> None:
    """Fence every coroutine of the current run without naming a successor.

    Shutdown calls this directly, over the tasks it is about to cancel; Stop comes
    through :func:`revoke_run_if_current`. The settlement right is deliberately left
    where it was: the run being stopped is still the one that owns its terminal row.
    """
    _RUN_GENERATIONS[campaign_id] = _RUN_GENERATIONS.get(campaign_id, 0) + 1


def revoke_run_if_current(campaign_id: str, run_id: str | None) -> bool:
    """Fence the run ``run_id`` names; ``False`` means a newer run owns the campaign now.

    What Stop needs and :func:`revoke_run` cannot give it. The row Stop acts on came
    back from a thread, and in that gap the run it names can settle and a Start can
    publish a successor. Fencing unconditionally then hit the NEW run, and Stop went on
    to write its own ``stopping`` over the new run id and cancel the new task — after
    which its settle was refused, because the successor owned the settlement. The
    campaign stayed ``stopping`` with nothing playing it, every Start answered
    ``campaign_running``, and no further Stop could get it out.

    Asked of the in-memory owner and not of a row, because this test and the
    :func:`begin_run` that publishes a successor are both await-free: whichever runs
    first, the other sees it whole.

    ``None`` — and a campaign with no owner entry at all — is granted, the same reading
    :func:`claim_settlement` takes of the same map: there is nothing to fence against,
    and refusing would leave a live row nothing can settle.
    """
    owner = _RUN_OWNER.get(campaign_id)
    if run_id is not None and owner is not None and owner != run_id:
        return False
    revoke_run(campaign_id)
    return True


def run_is_current(campaign_id: str, generation: int) -> bool:
    """Is ``generation`` still the live run of this campaign?

    The predicate ``_seams.run_scope`` is handed, checked before and after every
    external call.
    """
    return _RUN_GENERATIONS.get(campaign_id, 0) == generation


def claim_settlement(campaign_id: str, run_id: str) -> bool:
    """Take the right to write this campaign's terminal row.

    Refused in exactly one case: a DIFFERENT run owns the campaign. That is the late
    finisher trying to write ``done`` over its successor's ``running``.

    No entry at all is granted, not refused, and that is what unwedges the Stop race:
    Stop reads a live campaign, the run task settles in the gap before Stop writes
    ``stopping``, and that write resurrects a terminal row. The task took the entry
    away when it settled, so Stop's fallback finds nothing — and it is precisely the
    run whose settlement is being repeated, so repeating it (the same terminal row,
    the same release) is what the campaign needs rather than a refusal that would
    leave it ``stopping`` until a restart.

    Contains no ``await``, so the check and the take cannot be straddled.
    """
    owner = _RUN_OWNER.get(campaign_id)
    if owner is not None and owner != run_id:
        return False
    _RUN_OWNER.pop(campaign_id, None)
    return True


def reset_for_tests() -> None:
    _LLM_CALLS.clear()
    _REPLY_ATTEMPTS.clear()
    _CHAT_ATTEMPTS.clear()
    _KEY_WARNED.clear()
    _GENERATING.clear()
    _STARTING.clear()
    _RUN_GENERATIONS.clear()
    _RUN_OWNER.clear()
