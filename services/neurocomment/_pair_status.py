"""What ONE ``neurocomment_readiness`` row says about commenting on that channel.

Two callers need this ladder and it is written once: ``board`` folds it over a whole
channel's rows for the badge, ``engine`` asks it about one pair at a time for a selection
miss. A second parallel copy is what this module exists to prevent, and the cost of a caller
having no share of it is concrete — such a caller can report only ``not_ready``, which the
operator log renders as "не допущен в чат, снят оператором или забанен": three different
situations, one useless sentence.

What stays with the callers, because a readiness row cannot answer it:

* ``board`` keeps its CHANNEL-level pre-checks (comments switched off, a #147 pause, a
  guardian-bot challenge row) and its aggregation of the pairs into one badge;
* ``_gates`` keeps the gates that are not about this row at all (flood cooldown, the
  warming hand-off, trust/health, quota) and the operator skip (#148), which the channel
  row deliberately does not show — see ``board._channel_status``.

The vocabulary is mostly borrowed, not invented: every rung of the ladder below except the
``not_ready`` catch-all — which the board maps to ``throttled`` — is already a
``ChannelStatus`` (``schemas.neurocomment_board``), so the SPA's badge map renders it. That
buys the LOG nothing: ``logEventReason`` is a separate map, and the two codes
``REPORT_ORDER`` carries for the engine alone, ``human_skipped`` and ``not_handed_off``, are
not ``ChannelStatus`` at all — eight reasons had to be written into both locales for this
split. A new rung means new wording in both again.

The access-loss predicates come from ``_rejoin`` rather than being re-derived here — what
"lost access" means, and how much budget it buys, is that rule's to define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from services.neurocomment import _rejoin

if TYPE_CHECKING:
    from datetime import datetime

    from schemas.neurocomment import NeurocommentReadiness

# Spelled out because ``board`` renders these through two plain lookups (``_AS_CHANNEL``,
# ``_CHANNEL_PRIORITY``) that fall back to ``throttled``: a rung in neither would badge
# wrongly with no type error, no failing test and no missing translation to notice it. A
# rung added below has to be added here too, and ``test_neurocomment_board`` then fails
# until the board knows what to do with it.
PairBlock = Literal[
    "banned",
    "chat_restricted",
    "rejoin_exhausted",
    "rejoining",
    "join_failed",
    "join_by_request",
    "not_ready",
]

# Severity order for a caller holding several pairs blocked for different reasons — the
# order of the operator's ANSWER, not of this ladder's rungs. Two rules, applied in this
# order. First, how far the pair got: ``not_handed_off`` is reached only after the readiness
# row has already said ready, so that pair cleared strictly more gates than any rung below
# it and speaks first. Second, at comparable progress, TERMINAL before TRANSIENT — a
# transient block announces itself, because the channel starting to work IS the
# announcement, while a permanent loss never does. An earlier order put the self-resolving
# rungs on top and hid exactly that: four pairs auto-banned beside one muted pair reported
# only ``chat_restricted``, and a mute has no timer that flips it, so ``banned`` never
# reached the log at all. Hence the verdicts nothing else will tell the operator about —
# ``banned``, the operator's own skip, ``chat_restricted`` (a mute or an unconfirmed ban,
# NOT only a captcha the solver is still working), then the two that have run out of ways
# back in — above the self-resolving ones, which need no announcement. ``not_ready`` stays
# last: it is the rung that says nothing more specific.
# It lives with the vocabulary it orders; ``engine`` prefixes its own gates (quota,
# cooldown, health) and reads the whole thing as one list, which is why the three rungs the
# engine adds AROUND this ladder — ``no_data``, the operator skip and the warming hand-off
# — are ordered in here rather than left for it to interleave by hand.
REPORT_ORDER = (
    "not_handed_off",
    "banned",
    "human_skipped",
    "chat_restricted",
    "rejoin_exhausted",
    "join_failed",
    "rejoining",
    "join_by_request",
    "no_data",
    "not_ready",
)


def pair_block_reason(
    readiness: NeurocommentReadiness, now: datetime | None = None
) -> PairBlock | None:
    """Why this pair cannot comment on this channel, or ``None`` while it can.

    Rung for rung the order ``board._channel_status`` walked as an ``any()`` ladder, which
    is what makes the two answers identical: the aggregate re-checked ``banned`` over rows
    it had already cleared of ``ready``, so per-row precedence and channel-wide precedence
    are the same ordering. ``banned`` (#30) sits ABOVE ``ready`` here rather than below it
    because the engine must never select a pair whose sticky ban was left beside a stale
    ``ready=1`` — the board can't tell the difference, since it answers "ready" for the
    channel the moment any row carries it.

    The two readings of the unjoined-but-``captcha_passed`` sentinel are ``_rejoin``'s to
    split, not this module's: a pair with re-join budget left is walking itself back in
    (``rejoining``), one that has spent it — or that Telegram says is unreachable at all —
    is ``rejoin_exhausted``. What remains under that sentinel is the pair no re-join rule
    will touch, i.e. the terminal ``join_failed``. ``bot_challenge`` is NOT here: telling it
    from ``chat_restricted`` needs the channel's challenge row, which no readiness row
    carries, so the caller that has it makes that call.

    ``now`` is the caller's own clock for the re-join budget (the engine's per-post
    instant); ``None`` reads the wall clock, which is what a board poll wants.
    """
    if readiness.banned:
        return "banned"
    if readiness.ready:
        return None
    if readiness.joined and not readiness.captcha_passed:
        return "chat_restricted"
    if _rejoin.access_lost(readiness):
        return "rejoin_exhausted" if _rejoin.exhausted(readiness, now) else "rejoining"
    if not readiness.joined:
        return "join_failed" if readiness.captcha_passed else "join_by_request"
    # Joined, past the bot check, and still not ready: nothing on the row says why. The
    # board has always badged this ``throttled``; for the engine it is the last rung of the
    # selection ladder, which is what ``not_ready`` now means and nothing more.
    return "not_ready"
