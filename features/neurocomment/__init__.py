"""NiceGUI Neurocomment page (issue #119; redesign #155-line).

UI-thin per non-negotiable #1: every handler validates input, calls a
``services.neurocomment`` function, and re-renders. No business logic here — the
campaign/channel/account wiring, onboarding, runtime start/stop and the board
read model all live in ``services/``. Mirrors the *style* of ``features/warming``
(animated pipeline rail + live status + card layout) but imports nothing from it
(no cross-feature imports — non-negotiable #1); the small keyframe set warming
keeps in its own ``__init__`` is re-stated here under a ``tb-nc-`` prefix so the
neurocomment animations travel with this page without coupling the two features.

The whole page is excluded from coverage (``pragma: no cover``) like the other
feature pages — it is exercised manually; the pure helpers it calls are unit-tested.
"""

from __future__ import annotations

from nicegui import ui

from features.neurocomment._page import render_neurocomment_page

__all__ = ["register_neurocomment_page"]

# Neurocomment pipeline animations. Registered once via ``ui.add_css(shared=True)``
# so the keyframes reach every client. ``--i`` (the 0-based step index) is set
# inline per rail node to stagger the launch cascade and the running "wave".
#
#   .tb-nc-rail              the 6-step rail container
#   .tb-nc-rail.tb-nc-on     running — connectors flow, nodes pulse + launch-pop
#   .tb-nc-conn / .tb-nc-node  one connector bar / one step circle
#   .tb-nc-dot               the live-status blinking dot
#   .tb-nc-flash             one-shot highlight when a live counter ticks up
_NC_CSS = """
@keyframes tb-nc-flow {
    0% { background-position: 0% 50%; opacity: 0.5; }
    50% { background-position: 100% 50%; opacity: 1; }
    100% { background-position: 0% 50%; opacity: 0.5; }
}
@keyframes tb-nc-wave {
    0%, 100% { box-shadow: 0 0 0 0 rgba(99, 102, 241, 0); }
    50% { box-shadow: 0 0 0 7px rgba(99, 102, 241, 0.16); }
}
@keyframes tb-nc-pop {
    0% { opacity: 0; transform: scale(0.4); }
    60% { opacity: 1; transform: scale(1.14); }
    100% { opacity: 1; transform: scale(1); }
}
@keyframes tb-nc-blink {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.35; transform: scale(1.5); }
}
@keyframes tb-nc-flash {
    0% { background-color: rgba(16, 185, 129, 0.35); }
    100% { background-color: transparent; }
}
.tb-nc-conn {
    height: 3px;
    border-radius: 9999px;
    background: #e2e8f0;
}
.tb-nc-rail.tb-nc-on .tb-nc-conn {
    background: linear-gradient(
        90deg,
        rgba(99, 102, 241, 0) 0%,
        rgba(99, 102, 241, 0.9) 50%,
        rgba(99, 102, 241, 0) 100%
    );
    background-size: 200% 100%;
    animation: tb-nc-flow 1.6s linear infinite;
}
.tb-nc-rail.tb-nc-on .tb-nc-node {
    animation: tb-nc-pop 0.5s ease both, tb-nc-wave 2.2s ease-in-out infinite;
    animation-delay: calc(var(--i, 0) * 0.1s), calc(0.5s + var(--i, 0) * 0.28s);
}
.tb-nc-dot { animation: tb-nc-blink 1.3s ease-in-out infinite; }
.tb-nc-flash { animation: tb-nc-flash 1.1s ease-out; }
.tb-nc-log {
    background: #09090b;
    border: 1px solid #1f1f23;
    border-radius: 8px;
    padding: 6px 8px;
    max-height: 16rem;
    overflow-y: auto;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
    font-size: 11px;
    line-height: 1.4;
}
.tb-nc-log-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 2px 0;
    border-bottom: 1px solid #18181b;
    white-space: nowrap;
}
.tb-nc-log-row:last-child { border-bottom: 0; }
.tb-nc-log-time { color: #52525b; font-variant-numeric: tabular-nums; flex: 0 0 auto; }
.tb-nc-log-msg { color: #e4e4e7; flex: 0 0 auto; }
.tb-nc-log-kv { color: #71717a; overflow: hidden; text-overflow: ellipsis; }
.tb-nc-log-empty { color: #52525b; font-style: italic; }
.tb-nc-log-ok { color: #4ade80; }
.tb-nc-log-warn { color: #fbbf24; }
.tb-nc-log-err { color: #f87171; }
"""

ui.add_css(_NC_CSS, shared=True)


def register_neurocomment_page() -> None:  # pragma: no cover
    @ui.page("/neurocomment", title="Telebuba — Нейрокомментинг")
    async def neurocomment_page() -> None:
        await render_neurocomment_page()
