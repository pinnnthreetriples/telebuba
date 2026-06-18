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

from services.warming._chat import _sanitize_chat_text
from services.warming._cycle import _human_delay, run_one_cycle
from services.warming._loop import run_loop_iteration
from services.warming._runtime import (
    _RUNTIME,
    UnknownAccountError,
    WarmingNotReadyError,
    _initial_delay_seconds,
    _loop_sleep_seconds,
    _stop_warming_locked,
    account_lock,
    reconcile_warming_runtime,
    shutdown_warming_runtime,
    start_warming,
    stop_warming,
)
from services.warming.board import load_board
from services.warming.channels import add_channels, list_channels, remove_channel
from services.warming.pacing import (
    _in_quiet_hours,
    _local_now,
    _proxy_snapshot,
    _quiet_hours_end_at,
    _roll_daily,
    _seconds_until,
    _shift_to_active_hours,
    compute_intensity,
    evaluate_readiness,
)
from services.warming.settings_store import load_settings, save_settings

__all__ = [
    "_RUNTIME",
    "UnknownAccountError",
    "WarmingNotReadyError",
    "_human_delay",
    "_in_quiet_hours",
    "_initial_delay_seconds",
    "_local_now",
    "_loop_sleep_seconds",
    "_proxy_snapshot",
    "_quiet_hours_end_at",
    "_roll_daily",
    "_sanitize_chat_text",
    "_seconds_until",
    "_shift_to_active_hours",
    "_stop_warming_locked",
    "account_lock",
    "add_channels",
    "compute_intensity",
    "evaluate_readiness",
    "list_channels",
    "load_board",
    "load_settings",
    "reconcile_warming_runtime",
    "remove_channel",
    "run_loop_iteration",
    "run_one_cycle",
    "save_settings",
    "shutdown_warming_runtime",
    "start_warming",
    "stop_warming",
]
