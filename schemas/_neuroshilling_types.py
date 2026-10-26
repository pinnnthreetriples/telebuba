"""The neuroshilling domain's closed vocabularies (statuses, owners, refusal codes).

Split out of ``schemas.neuroshilling``, which re-exports every name.
"""

from __future__ import annotations

from typing import Literal

NeuroshillingMode = Literal["campaign", "revive"]
NeuroshillingScenarioStatus = Literal["draft", "approved"]
NeuroshillingRunMode = Literal["sequential", "parallel"]
NeuroshillingAutoresponder = Literal["off", "neurodialog"]
NeuroshillingReplyActivity = Literal["calm", "medium", "active"]
NeuroshillingStatus = Literal["idle", "running", "stopping", "done", "failed"]
NeuroshillingAccountState = Literal["active", "banned", "replaced"]
# Where ONE account stands with ONE target. Not a property of the target: a chat id
# is resolved out of the account's own session entity cache, and that cache is a
# separate file per account, so "we are in this chat" is only ever true of an
# account that itself joined. The five non-``pending`` states are the five outcomes
# Telegram answers a join with, and only two of them mean "play the dialogue here".
NeuroshillingPresenceState = Literal[
    "pending",
    "joined",
    # The chat gates entry and our request is queued: the account is NOT inside, so
    # running the steps here would fail every one of them.
    "pending_approval",
    # An expired or revoked invite, a chat that refuses us, or a peer shape whose
    # message ids are not shared between accounts.
    "refused",
    # Telegram rate-limited this account; it stops for the whole run.
    "flooded",
    # The account is out of the campaign entirely (e.g. at Telegram's 500-chat
    # ceiling), which is an ACCOUNT condition rather than a fault of this target.
    "retired",
]
# Who is holding an account right now, as the accounts modal reports it.
_HOLDERS = Literal["warming", "neuroshilling", "neurocomment", "chat_broadcast", "user_parser"]
NeuroshillingBusyOwner = Literal[_HOLDERS, "masstagging", "bulk_messages"]
# One row of the send journal. ``pending`` is written BEFORE the dispatch and is the
# only state that consumes a quota slot without anything having been published yet —
# which is exactly why the quota predicate counts it. A row left ``pending`` after the
# dispatch returned means the request was already on the wire when the connection died
# and Telegram may have applied it, so it is never retried, and nothing the run does
# deletes it. Shortening the scenario does: ``_scenario._drop_steps_beyond`` removes the
# journal rows of steps that no longer exist, which a live run cannot reach because
# every scenario write is refused while the campaign is running.
NeuroshillingMessageStatus = Literal["pending", "sent", "failed", "skipped"]

NeuroshillingRefusalCode = Literal[
    "campaign_running",
    "campaign_changed",
    "too_many_targets",
    "no_targets",
    "not_enough_accounts",
    "account_busy",
    "role_without_account",
    "unknown_role",
    "run_mode_not_supported",
    "scenario_not_approved",
    "scenario_invalid",
    # The two halves of the outbound content filter, named apart because the operator's
    # edit differs: one line carries a link, the other carries a word the configured
    # list forbids. Folded into ``scenario_invalid`` they would send the operator
    # hunting through roles and delays for a fault that is in the text.
    "scenario_text_has_link",
    "scenario_text_forbidden_word",
    "llm_daily_limit_reached",
    "llm_unavailable",
    "generation_in_progress",
    "target_is_basic_group",
    # The same shared-id-sequence fault as the code above, named apart because the
    # operator's next move differs: a basic group can be upgraded to a supergroup,
    # while a private chat is not a group at all and has to be replaced.
    "target_is_private_chat",
    "media_source_unreachable",
    # Distinct from the one above, because the operator's next move is opposite: the
    # media check never got an answer (a flood wait, a dead socket), so the link is
    # not the problem and editing it fixes nothing.
    "media_check_unavailable",
    # The media slot names a reaction step. Named apart from ``scenario_invalid``
    # because the scenario is not what is wrong: every row of it is legal, and the
    # field to change is the media slot on the same card.
    "media_step_not_message",
    # There is a media link but the slot names no step, or names one past the end of
    # the dialogue. Apart from the code above for the same reason, and it is the
    # ordinary state right after a generation, which clears the slot.
    "media_step_missing",
    # A rostered account is the running neurocomment listener. Named apart from
    # ``account_busy`` because that code covers three holders at once and its copy can
    # only list them; this one names the single hold it means and the switch that ends
    # it. WHICH rostered account it is comes from the picker, which greys that row out
    # (``campaigns._busy_owners``) — a refusal code carries no account id.
    "account_is_listener",
]
