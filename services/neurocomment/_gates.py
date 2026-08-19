"""The account gates, and the re-check ``comment_mode='reply'`` owes them.

Split out of ``engine`` for the file-size budget, and split HERE rather than at some other
seam because these are the gates with two callers whose clocks disagree. On the immediate
path every gate is read microseconds before the send, so nothing can change in between. A
parked post is sent one to a hundred and twenty minutes later, and every one of those gates
is a statement about NOW: a paused channel, a flood cooldown, a revoked readiness row, a
filled quota. Asking again is the whole job of :func:`resume_refusal`, and it asks through
the very ladder ``engine._select_account`` scores candidates with — :func:`_is_eligible`
and :func:`_account_block_reason` below — because a hand-written second list of gates over
in the wait is exactly how the two paths would drift apart on the next edit to either.

The whole ladder lives here, not just the quota rungs: the rungs are read as one ordered
sequence (:data:`_BLOCK_PRIORITY`), and a rung whose order lived in one module while its
verdict lived in another is a rung nothing can keep honest. ``engine`` keeps the bulk pool
LOAD, which is I/O and its own concern; this module keeps every judgement made from it.

Refusals are reported with the immediate path's own event codes; this module coins none.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from core.db import (
    count_account_channel_comments_since,
    count_account_comments_since,
    fetch_channel_paused_until,
    list_campaign_accounts,
    list_campaign_channels,
)
from services import _account_owner
from services.neurocomment import _pair_status, _state
from services.neurocomment._pins import serving_accounts
from services.trust import account_trust_score_from
from services.warming.pacing import evaluate_readiness

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.neurocomment import CommentRecord, NeurocommentCampaign, NeurocommentSettings

    # Type-only, and deliberately this direction: ``_SelectionPool`` is what ``engine``'s
    # bulk loader RETURNS, so it belongs beside that loader, while the runtime import runs
    # one way only (``engine`` imports this module). Naming the type here costs nothing at
    # import time; owning it here would drag the loader and its patch seams along with it.
    from services.neurocomment.engine import _SelectionPool


def _quota_block_reason(
    account_id: str,
    limits: NeurocommentSettings,
    hourly: dict[str, int],
    daily: dict[str, int],
) -> str | None:
    """Which cap the account has reached, or ``None`` while under both.

    ``quota_hour`` (per-account/hour) is reported before ``quota_day`` (per-channel/
    day) when both are full, so the log names the specific limit the operator hit.
    Quota counts in-flight claims AND delivered comments (status in claimed/posted),
    so a burst arriving inside one account's reply-delay window can't stack past the
    cap — each claim consumes quota the moment it is won.
    """
    if hourly.get(account_id, 0) >= limits.max_comments_per_hour:
        return "quota_hour"
    day_cap = limits.max_comments_per_channel_per_day
    if day_cap > 0 and daily.get(account_id, 0) >= day_cap:
        return "quota_day"
    return None


def _already_held(created_at: str | None, since_iso: str) -> int:
    """1 when the caller's own row sits inside the window it is asking about, else 0.

    ISO-8601 UTC strings order as text exactly the way the SQL ``created_at >= since``
    behind the counters does, so this subtracts precisely the row that count added.
    """
    return 1 if created_at is not None and created_at >= since_iso else 0


async def _account_quota_block_reason(
    account_id: str,
    channel: str,
    limits: NeurocommentSettings,
    *,
    held_since: str | None = None,
) -> str | None:
    """Fresh single-account quota re-read (which cap, if any) under the lock before the claim.

    Reads only this account's fresh counts (not the whole fleet's grouped counts) — the
    re-check is per-account by nature, so the narrow single-account readers keep it
    O(1) rather than scanning every account's window. ``quota_hour`` outranks
    ``quota_day`` (same order as :func:`_quota_block_reason`).

    ``held_since`` is the ``created_at`` of a row the caller ALREADY owns in the counted
    statuses — a ``waiting`` post asking whether it may send now. Its slot was spent the
    moment it was parked, so leaving it in the count would refuse every parked post the
    instant the hourly cap is 1. The immediate path passes nothing: its row does not exist
    yet. Subtracting it is also what makes the re-read catch the leak this guards — the
    window is measured from ``created_at``, so a post parked ninety minutes ago has already
    dropped out of the hour it was admitted in, and the sibling admitted after it counts.
    """
    now = datetime.now(UTC)
    hour_ago = (now - timedelta(hours=1)).isoformat()
    hourly = await count_account_comments_since(account_id, hour_ago)
    if hourly - _already_held(held_since, hour_ago) >= limits.max_comments_per_hour:
        return "quota_hour"
    day_cap = limits.max_comments_per_channel_per_day
    if day_cap > 0:
        day_ago = (now - timedelta(days=1)).isoformat()
        daily = await count_account_channel_comments_since(account_id, channel, day_ago)
        if daily - _already_held(held_since, day_ago) >= day_cap:
            return "quota_day"
    return None


# Report the blocker of the account that passed the *most* gates. A maxed-out-but-healthy
# account (quota) means "add accounts / raise the cap", which is more useful than reporting
# some other account that is merely not warmed yet. The two quota caps report separately
# (which one is full) but both outrank the rest. Below them the same distance rule runs on,
# and where two blockers sit at comparable distance the TERMINAL one reports: a transient
# block announces itself when the channel starts working, a permanent loss never does. That
# half of the order ships with the vocabulary it orders, in ``_pair_status.REPORT_ORDER``
# — read its comment before moving a rung — and ends in ``not_ready``, the rung that says
# nothing more specific than that.
# ``busy_neuroshilling`` is the exception that goes FIRST despite passing the fewest gates:
# it is not a verdict about the account's health at all, it is "another feature is driving
# this session right now". Reporting a quota below it would send the operator to raise a
# cap that was never the reason, and hide the one fact that resolves itself.
_BLOCK_PRIORITY = (
    "busy_neuroshilling",
    "quota_hour",
    "quota_day",
    "cooldown",
    "unhealthy",
    *_pair_status.REPORT_ORDER,
)


def _account_block_reason(  # noqa: PLR0911 - one return per gate IS the ladder
    account_id: str,
    channel: str,
    channel_count: int,
    now: datetime,
    pool: _SelectionPool,
) -> str | None:
    """The first gate that makes one account ineligible, or ``None`` if it's eligible.

    Single source of the selection gate ladder — ``_is_eligible`` is just "no reason".
    The readiness row's own verdict is ``_pair_status``', shared with the board's channel
    badge; the rungs here are the ones no readiness row can answer.
    """
    # First, and re-read per post rather than once at listener start: selection runs on
    # EVERY incoming post, and a neuroshilling campaign can take the account between two
    # of them. A synchronous dict read, so the ``_SelectionPool`` promise of no
    # per-account I/O in this pass holds — there is nothing here to bulk-load.
    if _account_owner.owner_of(account_id) == "neuroshilling":
        return "busy_neuroshilling"
    if _state.in_cooldown(account_id, now, channel):
        return "cooldown"
    account = pool.accounts.get(account_id)
    readiness = pool.readiness.get(account_id)
    if account is None or readiness is None:
        return "no_data"
    if readiness.human_skipped:
        # Above the row ladder, and above ``ready``: the operator took this pair out of
        # service (#148), which holds even if a stale ``ready=1`` survived the skip. The
        # CHANNEL badge deliberately does not show it, which is why it is not shared.
        return "human_skipped"
    if (blocked := _pair_status.pair_block_reason(readiness, now)) is not None:
        return blocked
    warming = pool.states.get(account_id)
    if warming is None or not warming.promoted_to_nc or not warming.nc_handed_off:
        return "not_handed_off"
    if not _is_healthy(account, channel_count, now, pool):
        return "unhealthy"
    return _quota_block_reason(account_id, pool.limits, pool.hourly_counts, pool.daily_counts)


def _is_eligible(
    account_id: str, channel: str, channel_count: int, now: datetime, pool: _SelectionPool
) -> bool:
    return _account_block_reason(account_id, channel, channel_count, now, pool) is None


def _selection_block_reason(
    account_ids: list[str], channel: str, channel_count: int, now: datetime, pool: _SelectionPool
) -> str:
    """Summarise why no account was eligible as the highest-priority binding blocker."""
    reasons = {
        reason
        for account_id in account_ids
        if (reason := _account_block_reason(account_id, channel, channel_count, now, pool))
    }
    return next((reason for reason in _BLOCK_PRIORITY if reason in reasons), "not_ready")


def _is_healthy(
    account: AccountRead,
    channel_count: int,
    now: datetime,
    pool: _SelectionPool,
) -> bool:
    """Warming readiness gate + Trust Score, scored from already-loaded signals."""
    spam = pool.spam.get(account.account_id)
    fingerprint = pool.fingerprints.get(account.account_id)
    trust = account_trust_score_from(
        account=account,
        record=pool.states.get(account.account_id),
        spam=spam,
        lang_code=fingerprint.system_lang_code if fingerprint else None,
        now=now,
    )
    if trust.score < pool.limits.min_trust_score:
        return False
    health = evaluate_readiness(account, channel_count, spam=spam, trust_score=trust)
    return health.ready


class Refusal(NamedTuple):
    """A gate saying no, in the words the immediate path already logs that gate with."""

    event: str
    reason: str | None = None  # the selection ladder's blocker, for the account gates


async def resume_refusal(
    campaign: NeurocommentCampaign,
    row: CommentRecord,
    limits: NeurocommentSettings,
    now: datetime,
) -> Refusal | None:
    """Every gate below the campaign, re-asked for a parked post about to send; ``None`` = go.

    The caller owns the campaign gate itself (``fetch_active_campaign_for_channel``, which
    is where ``status == 'active'`` lives) because it needs the campaign either way. From
    there this is the rest of ``engine._handle_new_post``'s ladder, reached through the same
    functions rather than restated: the channel pause (#147), the account still serving this
    channel, and ``_account_block_reason`` — cooldown, readiness, trust, quota — over a pool
    loaded for the one account that already holds the row.

    The account is given, not re-selected: it has held this post's quota slot since it was
    parked, and handing the post to a fresher account would mean abandoning that claim to
    win another. The filter verdicts are not re-run either — ``_filters`` judges the post's
    own text and media, and neither changes while a post sits.
    """
    if _state.channel_paused(await fetch_channel_paused_until(row.channel), now):
        return Refusal("neurocomment_channel_cooled")
    links = (await list_campaign_accounts(campaign.campaign_id)).links
    if row.account_id not in serving_accounts(links, row.channel):
        return Refusal("neurocomment_no_account_available", "no_accounts_linked")
    channel_count = max(1, len((await list_campaign_channels(campaign.campaign_id)).links))
    # The one reference this module makes to ``engine``, and late so the runtime import stays
    # one-way: ``engine`` imports the gates above at load, so a module-scope import back would
    # close the cycle. Only the bulk LOAD is borrowed — the ladder that judges it is local.
    from services.neurocomment import engine  # noqa: PLC0415

    pool = await engine._load_selection_pool(  # noqa: SLF001 - this domain's own gate ladder.
        campaign.campaign_id, row.channel, [row.account_id], now, limits
    )
    # The ladder's last rung is quota, scored off those grouped counts — which include the
    # parked row itself. Emptied here so the fresh, own-slot-aware re-read below is the only
    # quota verdict; left in, they would refuse every resumed post the moment a cap is 1.
    reason = _account_block_reason(
        row.account_id,
        row.channel,
        channel_count,
        now,
        pool._replace(hourly_counts={}, daily_counts={}),
    ) or await _account_quota_block_reason(
        row.account_id, row.channel, limits, held_since=row.created_at
    )
    return None if reason is None else Refusal("neurocomment_no_account_available", reason)
