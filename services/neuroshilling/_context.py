"""What one pass of a campaign carries with it, read once when the run starts.

Its own module because both halves of the step pipeline need it — ``_steps`` picks the
speaker and reserves the row, ``_dispatch`` publishes it and records the outcome — and
either of them owning the type would make the other import its sibling backwards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingCampaign
    from schemas.neuroshilling_scenario import NeuroshillingStep


class RunContext(NamedTuple):
    """The campaign, its dialogue and its cast, plus the run's own halt set.

    ``halted`` is mutable and deliberately so: it is consulted on every step of every
    target, and re-reading the presence table for that would be a query per step. Every
    verdict that puts an account in it is written down at the same moment by
    ``_telegram.record_send_verdict`` — a flood as ``flooded``, a dead session or a ban
    as ``retired`` — so a restart does not lose what this set is a cache of.

    ``by_role`` is mutable for one caller only: ``_substitution.substitute`` swaps a
    banned account for a reserve one in place, so the very next step is dealt to the
    stand-in instead of finding the role a voice short.

    ``banned`` is the narrower half of ``halted`` — the verdicts a substitute can
    actually help with — and it maps the account to WHICH of them it was. A flood is a
    wait and a chat refusal meets the substitute identically, so neither belongs here;
    the two that do are told apart because only one of them is evidence about the chat.
    ``_steps`` reads it for the account that just spoke and takes that one out, and
    takes a stand-in banned on its own replay out too, so nothing is ever left in the
    map for a later step to act on: every entry is answered by the step that put it
    there.

    ``our_user_ids`` are the Telegram user ids of the campaign's own accounts, read
    once here because they never change during a run. They are the poller's last
    answer to "is this one of ours?" when neither of the other two can be: Telethon's
    ``out`` flag only speaks for the reading account, and the id-based answers need a
    message id we did not always get back. An account the operator has never checked
    has no stored user id and is simply absent — the set narrows the question, it does
    not pretend to settle it.

    ``banned_in`` is what keeps ONE hostile chat from costing one reserve per player
    of the role. Keyed by target and holding the accounts Telegram BANNED while acting
    in that target — a logged-out session is not evidence about a chat and is left out
    — it is the evidence ``_steps`` weighs: a single ban is about the account, a second
    one in the same chat is about the chat, and the chat is abandoned rather
    than fed another reserve. It outlives a revive cycle on purpose —
    ``_revive._cycle_context`` only ``_replace``s the run id, so the dict is shared —
    because a chat that banned two accounts last cycle bans them this cycle too.
    """

    campaign: NeuroshillingCampaign
    run_id: str
    steps: list[NeuroshillingStep]
    # position -> step, for the reply-anchor walk and the reaction's target.
    by_position: dict[int, NeuroshillingStep]
    # role_id -> the accounts that may speak that part.
    by_role: dict[str, list[str]]
    halted: set[str]
    # account_id -> the verdict that finished it: ``account_banned`` or ``account_dead``.
    banned: dict[str, str]
    # target -> the accounts Telegram banned while they were acting in that target.
    banned_in: dict[str, set[str]]
    # The Telegram user ids of this campaign's own accounts, for the poller.
    our_user_ids: frozenset[int] = frozenset()
