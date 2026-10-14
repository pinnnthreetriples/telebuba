"""Account-warming engine (package).

Pure business logic per non-negotiable #11: the warming algorithm, human-like
pacing, FloodWait handling, and inter-account chat. NiceGUI handlers in
``features/warming`` are thin delegators; the same functions drive a scheduler.

Runtime model. Warming is a *continuous randomised loop* per account (cycle ->
12-30h sleep -> repeat), not a cron job, so each running account owns an
:class:`asyncio.Task` in ``_runtime._RUNTIME``. ``run_one_cycle`` is the testable
unit; ``_runtime._warming_loop`` is the long-running wrapper.

Package layout. Side-effect-light slices: ``channels`` (input parsing),
``settings_store`` (settings row), ``board`` (kanban read model), ``pacing``
(scheduling/intensity). The engine proper is split across ``_state`` (state
transitions), ``_cycle`` (the cycle), ``_chat`` (Gemini chat), ``_runtime``
(lifecycle + loop). The injectable seams (``execute`` / ``generate_text`` /
``refresh_spam_status`` / ``_rng``) live in ``_seams`` so tests patch them once.

This module only re-exports the public API and the internal helpers that tests
reach as ``services.warming._x``.
"""

from __future__ import annotations

from services.warming._chat_text import _sanitize_chat_text
from services.warming._console_start import rewarm_account, start_warming_with_consent
from services.warming._cycle import run_one_cycle
from services.warming._exclusion import AccountUnavailableError
from services.warming._handoff_spam import HandoffSpamRefusedError
from services.warming._loop import run_loop_iteration
from services.warming._runner import _initial_delay_seconds, _loop_sleep_seconds
from services.warming._runtime import (
    _ACCOUNT_LOCKS,
    _RUNTIME,
    AccountIsListenerError,
    NotGraduatedError,
    UnknownAccountError,
    WarmingNotReadyError,
    WarmingTaskNotQuiescentError,
    _stop_warming_locked,
    account_lock,
    handoff_to_neurocomment,
    keep_spam_blocked,
    promote_to_neurocomment,
    reconcile_warming_runtime,
    shutdown_warming_runtime,
    start_warming,
    stop_warming,
)
from services.warming._spam_block import NotSpamBlockedError
from services.warming._start_options import preview_start
from services.warming._start_state import WarmingDeviceUnavailableError
from services.warming._steps import _human_delay
from services.warming.board import list_warmed_accounts, load_board
from services.warming.channels import (
    UnknownChannelError,
    add_channels,
    label_channel,
    list_channels,
    remove_channel,
)
from services.warming.daily_stats import load_daily_stats
from services.warming.pacing import (
    _in_quiet_hours,
    _proxy_snapshot,
    _roll_daily,
    _seconds_until,
    _shift_to_active_hours,
    compute_intensity,
    evaluate_readiness,
    persona_next_run_seconds,
)
from services.warming.settings_store import lift_dm_pause, load_settings, save_settings

__all__ = [
    "_ACCOUNT_LOCKS",
    "_RUNTIME",
    "AccountIsListenerError",
    "AccountUnavailableError",
    "HandoffSpamRefusedError",
    "NotGraduatedError",
    "NotSpamBlockedError",
    "UnknownAccountError",
    "UnknownChannelError",
    "WarmingDeviceUnavailableError",
    "WarmingNotReadyError",
    "WarmingTaskNotQuiescentError",
    "_human_delay",
    "_in_quiet_hours",
    "_initial_delay_seconds",
    "_loop_sleep_seconds",
    "_proxy_snapshot",
    "_roll_daily",
    "_sanitize_chat_text",
    "_seconds_until",
    "_shift_to_active_hours",
    "_stop_warming_locked",
    "account_lock",
    "add_channels",
    "compute_intensity",
    "evaluate_readiness",
    "handoff_to_neurocomment",
    "keep_spam_blocked",
    "label_channel",
    "lift_dm_pause",
    "list_channels",
    "list_warmed_accounts",
    "load_board",
    "load_daily_stats",
    "load_settings",
    "persona_next_run_seconds",
    "preview_start",
    "promote_to_neurocomment",
    "reconcile_warming_runtime",
    "remove_channel",
    "rewarm_account",
    "run_loop_iteration",
    "run_one_cycle",
    "save_settings",
    "shutdown_warming_runtime",
    "start_warming",
    "start_warming_with_consent",
    "stop_warming",
]
