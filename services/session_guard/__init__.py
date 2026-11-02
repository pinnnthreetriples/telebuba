"""Session guard: the background work that keeps an account's sessions ours.

Today it ends the old authorization of a re-keyed account once Telegram lets it; see
``_loop``.
"""

from __future__ import annotations

from services.session_guard._loop import run_guard_pass, shutdown_session_guard, start_session_guard

__all__ = ["run_guard_pass", "shutdown_session_guard", "start_session_guard"]
